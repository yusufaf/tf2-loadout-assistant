"""Rebuild the catalog and price caches in one validated, all-or-none step.

The caches used to be written as a side effect of the live tests, so a refresh was
test assertions plus whichever files happened to get written: a failing test left
a mix of new and old files, and the run spent an LLM key on the agent tests.

``tf2-loadout-refresh`` fetches everything first, validates it against fixed
floors and against the previous cache, writes it into a staging directory inside
``.cache/``, loads the staged files back the way the app will at boot, and only
then ``os.replace``s them into place. Every realistic failure (network, HTTP, bad
data, validation) happens before the first replace, so the previous cache is left
untouched. The replaces themselves are atomic per file, not as a group; a failure
between them is reported as a partial commit, naming which files are new.

``--catalog-if-changed`` keeps the catalog when GetSchemaOverview still points at
the items_game the cache was built from and the code that derived it hasn't
changed, which lets a scheduled job refresh prices daily without rebuilding the
15 MB schema. Exit codes tell such a job whether a retry can help.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TextIO

import httpx

from . import catalog
from .catalog import EQUIP_CACHE, SCHEMA_CACHE, CatalogService, save_catalog_cache
from .config import PROJECT_ROOT, load_env
from .pricing import KEY_DEFINDEX, PRICES_CACHE, PricingService
from .pricing_client import PricingClient, PricingError
from .schema_client import SchemaClient, SchemaError, items_game_hash

REQUIRED_KEYS = ("STEAM_API_KEY", "BACKPACK_TF_API_KEY")
# Prices last, so a partial commit never leaves a new catalog's items unpriced by
# an old file that covers them less well than the one that failed to land.
COMMIT_ORDER = (SCHEMA_CACHE, EQUIP_CACHE, PRICES_CACHE)


class ExitCode(IntEnum):
    OK = 0
    # A bug or unhandled data-shape drift: don't retry. Same code Python uses for an
    # uncaught exception, so 1 always means "not transient".
    INTERNAL_ERROR = 1
    USAGE = 2  # argparse's own code
    VALIDATION_FAILED = 3  # the data looked wrong; the previous cache is untouched
    # A key is missing, or an upstream rejected one with HTTP 401/403: fix config.
    BAD_KEY = 4
    FETCH_FAILED = 5  # network, HTTP 5xx/429/other 4xx, or an API error: retry later


@dataclass(frozen=True)
class Thresholds:
    """Floors always apply; ratios compare against the previous cache unless forced.

    Calibrated on 2026-11-21's refresh: 11,615 schema items, 1,916 cosmetics,
    2,726 priced items, and the key moving 57 -> 63 ref (x1.105).
    """

    min_schema_items: int = 5_000
    min_cosmetics: int = 1_000
    min_priced: int = 1_000
    min_schema_ratio: float = 0.9
    min_cosmetics_ratio: float = 0.9
    min_priced_ratio: float = 0.8  # backpack.tf coverage drifts more than the schema
    key_ratio_range: tuple[float, float] = (0.5, 2.0)


@dataclass(frozen=True)
class Baseline:
    """What the previous cache held; None for anything that couldn't be read."""

    schema_items: int | None = None
    cosmetics: int | None = None
    priced: int | None = None
    key_ref: float | None = None
    items_game_url: str | None = None
    derivation: int | None = None


class CacheValidationError(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class PartialCommitError(RuntimeError):
    def __init__(self, replaced: list[str], remaining: list[str]) -> None:
        super().__init__(f"replaced {replaced}, still old {remaining}")
        self.replaced = replaced
        self.remaining = remaining


def _best_effort(read):
    # The baseline only tunes the ratio checks and the catalog skip. A file that is
    # missing, stale or the wrong shape must not stop the refresh that repairs it.
    try:
        return read()
    except Exception:
        return None


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_baseline(cache_dir: Path) -> Baseline:
    cache_dir = Path(cache_dir)
    pricing = _best_effort(lambda: PricingService.from_cache(cache_dir))
    equip = _best_effort(lambda: _read_json(cache_dir / EQUIP_CACHE))
    return Baseline(
        schema_items=_best_effort(lambda: len(_read_json(cache_dir / SCHEMA_CACHE))),
        cosmetics=_best_effort(lambda: len(CatalogService.from_cache(cache_dir))),
        priced=_best_effort(lambda: len(pricing)) if pricing is not None else None,
        key_ref=(
            _best_effort(lambda: pricing.ref_value_for(KEY_DEFINDEX))
            if pricing is not None
            else None
        ),
        items_game_url=_best_effort(lambda: equip.get("items_game_url")),
        derivation=_best_effort(lambda: equip.get("derivation")),
    )


def _ratio_problem(what: str, new: float, old: float | None, minimum: float) -> str | None:
    if not old:
        return None
    if new / old < minimum:
        return f"{what} dropped from {old} to {new} (below {minimum:.0%} of the previous cache)"
    return None


def validate_catalog(
    built: CatalogService,
    schema_items: list[dict],
    baseline: Baseline,
    thresholds: Thresholds,
    *,
    force: bool,
) -> list[str]:
    problems = []
    if len(schema_items) < thresholds.min_schema_items:
        problems.append(
            f"only {len(schema_items)} schema items (need {thresholds.min_schema_items})"
        )
    if len(built) < thresholds.min_cosmetics:
        problems.append(f"only {len(built)} cosmetics (need {thresholds.min_cosmetics})")
    if not built.conflict_matrix:
        problems.append("items_game has no equip_conflicts matrix")
    if not force:
        for problem in (
            _ratio_problem(
                "schema items",
                len(schema_items),
                baseline.schema_items,
                thresholds.min_schema_ratio,
            ),
            _ratio_problem(
                "cosmetics", len(built), baseline.cosmetics, thresholds.min_cosmetics_ratio
            ),
        ):
            if problem:
                problems.append(problem)
    return problems


def validate_prices(
    pricing: PricingService, baseline: Baseline, thresholds: Thresholds, *, force: bool
) -> list[str]:
    problems = []
    if len(pricing) < thresholds.min_priced:
        problems.append(f"only {len(pricing)} priced items (need {thresholds.min_priced})")
    key = pricing.get(KEY_DEFINDEX)
    key_ok = key is not None and key.currency == "metal" and key.value > 0
    if not key_ok:
        problems.append(f"no metal price for the key (defindex {KEY_DEFINDEX})")
    if not force:
        problem = _ratio_problem(
            "priced items", len(pricing), baseline.priced, thresholds.min_priced_ratio
        )
        if problem:
            problems.append(problem)
        if key_ok and baseline.key_ref:
            low, high = thresholds.key_ratio_range
            ratio = key.value / baseline.key_ref
            if not low <= ratio <= high:
                problems.append(
                    f"key price moved from {baseline.key_ref} to {key.value} ref "
                    f"(x{ratio:.2f}, outside {low}-{high})"
                )
    return problems


@dataclass(frozen=True)
class RefreshReport:
    items_game_url: str
    items_game_hash: str | None
    catalog_rebuilt: bool
    schema_items: int | None  # None when the catalog was kept
    cosmetics: int | None
    priced: int
    key_ref: float | None
    baseline: Baseline

    def to_dict(self) -> dict:
        return {
            "catalog": {
                "status": "rebuilt" if self.catalog_rebuilt else "unchanged",
                "items_game_url": self.items_game_url,
                "items_game_hash": self.items_game_hash,
                "schema_items": self.schema_items,
                "cosmetics": self.cosmetics,
                "previous_cosmetics": self.baseline.cosmetics,
            },
            "prices": {
                "status": "refreshed",
                "priced": self.priced,
                "previous_priced": self.baseline.priced,
                "key_ref": self.key_ref,
                "previous_key_ref": self.baseline.key_ref,
            },
        }


def _progress(message: str) -> None:
    print(message, file=sys.stderr)


def _commit(staging: Path, cache_dir: Path) -> None:
    names = [name for name in COMMIT_ORDER if (staging / name).exists()]
    replaced: list[str] = []
    for i, name in enumerate(names):
        try:
            os.replace(staging / name, cache_dir / name)
        except OSError as e:
            raise PartialCommitError(replaced, names[i:]) from e
        replaced.append(name)


async def refresh(
    cache_dir: Path,
    schema_client: SchemaClient,
    pricing_client: PricingClient,
    *,
    catalog_if_changed: bool = False,
    force: bool = False,
    thresholds: Thresholds = Thresholds(),
) -> RefreshReport:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    baseline = read_baseline(cache_dir)

    _progress("fetching schema overview...")
    url = await schema_client.fetch_items_game_url()
    digest = items_game_hash(url)
    # Keep the catalog only if it was built from this items_game by this code and the
    # app can still load it: a skip must never keep a catalog the app refuses at boot.
    rebuild_catalog = not (
        catalog_if_changed
        and baseline.items_game_url == url
        and baseline.derivation == catalog.CATALOG_DERIVATION
        and baseline.cosmetics is not None
    )
    schema_items = items_game = built = None
    if rebuild_catalog:
        _progress("fetching schema items...")
        schema_items = await schema_client.fetch_all_items()
        _progress(f"fetching items_game ({digest or 'unknown hash'})...")
        items_game = await schema_client.fetch_items_game(url)
    else:
        _progress(f"catalog unchanged (items_game {digest or url})")
    _progress("fetching prices...")
    pricing = PricingService.from_response(await pricing_client.fetch_prices())

    problems = []
    if rebuild_catalog:
        built = CatalogService.build_from_sources(schema_items, items_game)
        problems += validate_catalog(built, schema_items, baseline, thresholds, force=force)
    problems += validate_prices(pricing, baseline, thresholds, force=force)
    if problems:
        raise CacheValidationError(problems)

    staging = Path(tempfile.mkdtemp(prefix=".refresh-", dir=cache_dir))
    try:
        if rebuild_catalog:
            save_catalog_cache(schema_items, items_game, staging, items_game_url=url)
        pricing.save_cache(staging)
        # The bytes about to go live are exactly what the app will boot from.
        try:
            if rebuild_catalog:
                CatalogService.from_cache(staging)
            PricingService.from_cache(staging)
        except Exception as e:
            raise CacheValidationError(
                [f"the staged cache did not load: {type(e).__name__}: {e}"]
            ) from e
        _commit(staging, cache_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return RefreshReport(
        items_game_url=url,
        items_game_hash=digest,
        catalog_rebuilt=rebuild_catalog,
        schema_items=len(schema_items) if rebuild_catalog else None,
        cosmetics=len(built) if rebuild_catalog else None,
        priced=len(pricing),
        key_ref=pricing.ref_value_for(KEY_DEFINDEX),
        baseline=baseline,
    )


def missing_keys(environ: Mapping[str, str]) -> list[str]:
    # Both are always needed: the overview call takes the Steam key even when the
    # catalog is kept.
    return [k for k in REQUIRED_KEYS if not environ.get(k, "").strip()]


def _fetch_failure(e: Exception) -> tuple[ExitCode, str]:
    # httpx's own messages carry the full URL, ?key= included, so they're never
    # printed: only the status and the URL with the key removed.
    if isinstance(e, httpx.HTTPStatusError):
        where = e.request.url.copy_remove_param("key")
        status = e.response.status_code
        if status in (401, 403):
            return ExitCode.BAD_KEY, f"key rejected: HTTP {status} from {where}"
        return ExitCode.FETCH_FAILED, f"fetch: HTTP {status} from {where}"
    if isinstance(e, httpx.HTTPError):
        return ExitCode.FETCH_FAILED, f"fetch: {type(e).__name__}"
    return ExitCode.FETCH_FAILED, f"fetch: {e}"  # our own SchemaError/PricingError text


async def run(
    cache_dir: Path,
    schema_client: SchemaClient,
    pricing_client: PricingClient,
    *,
    catalog_if_changed: bool = False,
    force: bool = False,
    thresholds: Thresholds = Thresholds(),
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> ExitCode:
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        report = await refresh(
            cache_dir,
            schema_client,
            pricing_client,
            catalog_if_changed=catalog_if_changed,
            force=force,
            thresholds=thresholds,
        )
    except CacheValidationError as e:
        print("refresh failed: validation; the previous cache is untouched", file=err)
        for problem in e.problems:
            print(f"  {problem}", file=err)
        return ExitCode.VALIDATION_FAILED
    except PartialCommitError as e:
        print(
            "refresh failed: partial commit: replaced "
            f"{', '.join(e.replaced) or 'nothing'}; still old: {', '.join(e.remaining)} "
            f"(cause: {type(e.__cause__).__name__})",
            file=err,
        )
        return ExitCode.INTERNAL_ERROR
    except (SchemaError, PricingError, httpx.HTTPError) as e:
        code, message = _fetch_failure(e)
        print(f"refresh failed: {message}", file=err)
        return code
    except Exception as e:
        # Frames only, never str(e): a runtime value such as a keyed URL could be in it.
        print(f"refresh failed: internal: {type(e).__name__}", file=err)
        print("".join(traceback.format_tb(e.__traceback__)), file=err, end="")
        return ExitCode.INTERNAL_ERROR
    print(json.dumps(report.to_dict(), indent=2), file=out)
    return ExitCode.OK


async def _amain(args: argparse.Namespace) -> ExitCode:
    async with SchemaClient() as schema_client, PricingClient() as pricing_client:
        return await run(
            args.cache_dir,
            schema_client,
            pricing_client,
            catalog_if_changed=args.catalog_if_changed,
            force=args.force,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tf2-loadout-refresh",
        description=(
            "Rebuild the catalog and price caches from Steam and backpack.tf, all or none. "
            "Needs STEAM_API_KEY and BACKPACK_TF_API_KEY. Prints a JSON summary on success."
        ),
        epilog=(
            "exit codes: 0 ok, 1 internal error, 2 usage, 3 validation failed, "
            "4 key missing or rejected, 5 fetch failed (retry later)"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=PROJECT_ROOT / ".cache",
        help="cache directory to refresh (default: the project's .cache/)",
    )
    parser.add_argument(
        "--catalog-if-changed",
        action="store_true",
        help="keep the catalog when items_game is unchanged; prices always refresh",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="skip the comparisons against the previous cache (the floors still apply)",
    )
    args = parser.parse_args(argv)
    # Before building the clients: they read their keys from os.environ.
    load_env()
    missing = missing_keys(os.environ)
    if missing:
        print(f"refresh failed: missing {', '.join(missing)}", file=sys.stderr)
        return ExitCode.BAD_KEY
    return int(asyncio.run(_amain(args)))
