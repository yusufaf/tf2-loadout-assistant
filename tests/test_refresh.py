"""tf2-loadout-refresh: fetch, validate, stage, then replace every cache file or none.

Every upstream is an httpx.MockTransport, so nothing here reaches the network. Never
call ``main`` past ``--help`` with the real ``load_env`` active: it would read the
real ``.env`` and go to the live APIs.
"""

from __future__ import annotations

import io
import json

import httpx
import pytest
import vdf

from tf2_loadout import refresh as refresh_module
from tf2_loadout.catalog import (
    CATALOG_DERIVATION,
    CatalogService,
    StaleCacheError,
)
from tf2_loadout.pricing import PricingService
from tf2_loadout.pricing_client import PricingClient
from tf2_loadout.refresh import (
    ExitCode,
    Thresholds,
    main,
    missing_keys,
    read_baseline,
    refresh,
    run,
)
from tf2_loadout.refresh import CacheValidationError
from tf2_loadout.schema_client import SchemaClient

HASH = "4619d9a62858cc1211292c217c50561d11cfefff"
SECRET = "SECRET123"
SMALL = Thresholds(min_schema_items=1, min_cosmetics=1, min_priced=1)
CACHE_FILES = ("schema_items.json", "equip.json", "prices.json")


def _items_game_url(digest: str = HASH) -> str:
    return f"http://media.steampowered.com/apps/440/scripts/items/items_game.{digest}.txt"


def _price(value, currency="metal"):
    return {
        "prices": {
            "6": {
                "Tradable": {
                    "Craftable": [
                        {"currency": currency, "value": value, "last_update": 1_700_000_000}
                    ]
                }
            }
        }
    }


class FakeUpstream:
    """Steam's schema endpoints, items_game.txt and backpack.tf, routed by path."""

    def __init__(self, hats=(116, 117, 118), key_value=60, digest=HASH):
        self.hats = list(hats)
        self.key_value = key_value
        self.key_currency = "metal"
        self.digest = digest
        self.calls: list[str] = []
        self.overrides: dict[str, httpx.Response] = {}

    def _route(self, path: str) -> str:
        if "GetSchemaItems" in path:
            return "schema"
        if "GetSchemaOverview" in path:
            return "overview"
        if "items_game." in path:
            return "items_game"
        return "prices"

    def handler(self, request: httpx.Request) -> httpx.Response:
        route = self._route(request.url.path)
        self.calls.append(route)
        if route in self.overrides:
            return self.overrides[route]
        if route == "schema":
            items = [
                {
                    "defindex": d,
                    "item_class": "tf_wearable",
                    "item_name": f"Hat {d}",
                    "used_by_classes": ["Scout"],
                }
                for d in self.hats
            ]
            return httpx.Response(200, json={"result": {"status": 1, "items": items}})
        if route == "overview":
            url = _items_game_url(self.digest)
            return httpx.Response(200, json={"result": {"status": 1, "items_game_url": url}})
        if route == "items_game":
            game = {
                "equip_conflicts": {"whole_head": {"hat": "1"}},
                "prefabs": {},
                "items": {str(d): {"name": f"Hat {d}", "equip_region": "hat"} for d in self.hats},
            }
            return httpx.Response(200, text=vdf.dumps({"items_game": game}))
        items = {"Mann Co. Supply Crate Key": {"defindex": [5021], **_price(self.key_value, self.key_currency)}}
        items.update({f"Hat {d}": {"defindex": [d], **_price(1.33)} for d in self.hats})
        return httpx.Response(200, json={"response": {"success": 1, "items": items}})

    def clients(self) -> tuple[SchemaClient, PricingClient]:
        transport = httpx.MockTransport(self.handler)
        return (
            SchemaClient(api_key=SECRET, transport=transport),
            PricingClient(api_key=SECRET, transport=transport),
        )


async def _refresh(cache_dir, fake: FakeUpstream, **kwargs):
    kwargs.setdefault("thresholds", SMALL)
    schema_client, pricing_client = fake.clients()
    async with schema_client, pricing_client:
        return await refresh(cache_dir, schema_client, pricing_client, **kwargs)


async def _run(cache_dir, fake: FakeUpstream, **kwargs):
    kwargs.setdefault("thresholds", SMALL)
    out, err = io.StringIO(), io.StringIO()
    schema_client, pricing_client = fake.clients()
    async with schema_client, pricing_client:
        code = await run(cache_dir, schema_client, pricing_client, out=out, err=err, **kwargs)
    return code, out.getvalue(), err.getvalue()


async def _seed(cache_dir, **fake_kwargs) -> FakeUpstream:
    fake = FakeUpstream(**fake_kwargs)
    await _refresh(cache_dir, fake)
    return fake


def _snapshot(cache_dir) -> dict[str, tuple[bytes, int]]:
    return {
        name: ((cache_dir / name).read_bytes(), (cache_dir / name).stat().st_mtime_ns)
        for name in CACHE_FILES
        if (cache_dir / name).exists()
    }


def _staging_dirs(cache_dir) -> list:
    return list(cache_dir.glob(".refresh-*"))


def _equip(cache_dir) -> dict:
    return json.loads((cache_dir / "equip.json").read_text(encoding="utf-8"))


async def test_refresh_writes_every_cache_file_and_they_load(tmp_path):
    report = await _refresh(tmp_path, FakeUpstream())

    assert all((tmp_path / name).exists() for name in CACHE_FILES)
    assert len(CatalogService.from_cache(tmp_path)) == 3
    assert PricingService.from_cache(tmp_path).ref_value_for(5021) == 60
    assert _equip(tmp_path)["items_game_url"] == _items_game_url()
    assert _staging_dirs(tmp_path) == []
    assert report.items_game_hash == HASH
    assert report.catalog_rebuilt is True


async def test_a_bad_items_game_download_leaves_the_previous_cache_untouched(tmp_path):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    fake.overrides["items_game"] = httpx.Response(502, text="<html>502 Bad Gateway</html>")

    with pytest.raises(httpx.HTTPStatusError):
        await _refresh(tmp_path, fake)

    assert _snapshot(tmp_path) == before
    assert _staging_dirs(tmp_path) == []


async def test_a_prices_failure_leaves_the_catalog_untouched_too(tmp_path):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    fake.hats = [116, 117, 118, 119]  # a new catalog that must not land on its own
    fake.overrides["prices"] = httpx.Response(500, text="down")

    with pytest.raises(httpx.HTTPStatusError):
        await _refresh(tmp_path, fake)

    assert _snapshot(tmp_path) == before


async def test_an_empty_schema_response_fails_validation(tmp_path):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    fake.overrides["schema"] = httpx.Response(200, json={"result": {"status": 1, "items": []}})

    with pytest.raises(CacheValidationError, match="cosmetics"):
        await _refresh(tmp_path, fake)

    assert _snapshot(tmp_path) == before
    assert _staging_dirs(tmp_path) == []


async def test_empty_prices_fail_validation(tmp_path):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    fake.overrides["prices"] = httpx.Response(200, json={"response": {"success": 1, "items": {}}})

    with pytest.raises(CacheValidationError, match="priced items"):
        await _refresh(tmp_path, fake)

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("currency", [None, "keys"])
async def test_a_missing_key_price_fails_validation(tmp_path, currency):
    fake = FakeUpstream()
    if currency is None:
        original = fake.handler

        def without_key(request):
            response = original(request)
            if fake._route(request.url.path) == "prices":
                data = response.json()
                del data["response"]["items"]["Mann Co. Supply Crate Key"]
                return httpx.Response(200, json=data)
            return response

        fake.handler = without_key
    else:
        fake.key_currency = currency

    with pytest.raises(CacheValidationError, match="key"):
        await _refresh(tmp_path, fake)

    assert _snapshot(tmp_path) == {}


async def test_a_large_drop_in_cosmetics_vs_previous_fails(tmp_path):
    fake = await _seed(tmp_path)
    fake.hats = [116]

    with pytest.raises(CacheValidationError) as excinfo:
        await _refresh(tmp_path, fake)

    assert any("cosmetics dropped from 3 to 1" in p for p in excinfo.value.problems)


async def test_force_skips_ratio_checks_but_not_floors(tmp_path):
    fake = await _seed(tmp_path)
    fake.hats = [116]

    report = await _refresh(tmp_path, fake, force=True)
    assert report.cosmetics == 1

    fake.overrides["prices"] = httpx.Response(200, json={"response": {"success": 1, "items": {}}})
    with pytest.raises(CacheValidationError, match="priced items"):
        await _refresh(tmp_path, fake, force=True)


async def test_a_key_price_jump_vs_previous_fails(tmp_path):
    fake = await _seed(tmp_path, key_value=60)
    fake.key_value = 200

    with pytest.raises(CacheValidationError, match="key price moved from 60"):
        await _refresh(tmp_path, fake)


async def test_no_previous_cache_skips_ratio_checks(tmp_path):
    assert read_baseline(tmp_path).cosmetics is None
    report = await _refresh(tmp_path, FakeUpstream(hats=[116]))
    assert report.cosmetics == 1


@pytest.mark.parametrize("case", ["stale-equip", "wrong-shape"])
async def test_an_unreadable_previous_cache_is_no_baseline(tmp_path, case):
    await _seed(tmp_path)
    if case == "stale-equip":
        equip = _equip(tmp_path)
        del equip["version"]
        (tmp_path / "equip.json").write_text(json.dumps(equip), encoding="utf-8")
    else:
        (tmp_path / "equip.json").write_text("[]", encoding="utf-8")
        (tmp_path / "prices.json").write_text(
            json.dumps({"version": 2, "prices": {"1": 5}}), encoding="utf-8"
        )

    baseline = read_baseline(tmp_path)

    assert baseline.cosmetics is None
    if case == "wrong-shape":
        assert baseline.priced is None
        assert baseline.items_game_url is None
    # The refresh that repairs the cache still runs.
    report = await _refresh(tmp_path, FakeUpstream())
    assert report.cosmetics == 3
    assert len(CatalogService.from_cache(tmp_path)) == 3


async def test_catalog_if_changed_skips_the_catalog_when_the_url_matches(tmp_path):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    fake.calls.clear()
    fake.key_value = 61

    report = await _refresh(tmp_path, fake, catalog_if_changed=True)

    assert "schema" not in fake.calls
    assert "items_game" not in fake.calls
    after = _snapshot(tmp_path)
    assert after["schema_items.json"] == before["schema_items.json"]
    assert after["equip.json"] == before["equip.json"]
    assert after["prices.json"] != before["prices.json"]
    assert report.catalog_rebuilt is False
    assert report.to_dict()["catalog"]["status"] == "unchanged"


async def test_catalog_if_changed_rebuilds_when_the_url_differs(tmp_path):
    fake = await _seed(tmp_path)
    fake.digest = "0" * 40
    fake.calls.clear()

    report = await _refresh(tmp_path, fake, catalog_if_changed=True)

    assert "schema" in fake.calls
    assert report.catalog_rebuilt is True
    assert _equip(tmp_path)["items_game_url"] == _items_game_url("0" * 40)


async def test_catalog_if_changed_rebuilds_when_the_previous_cache_has_no_url(tmp_path):
    fake = await _seed(tmp_path)
    equip = _equip(tmp_path)
    del equip["items_game_url"]
    (tmp_path / "equip.json").write_text(json.dumps(equip), encoding="utf-8")
    fake.calls.clear()

    report = await _refresh(tmp_path, fake, catalog_if_changed=True)

    assert "schema" in fake.calls
    assert report.catalog_rebuilt is True


@pytest.mark.parametrize("case", ["stale-version", "missing-schema"])
async def test_catalog_if_changed_rebuilds_a_previous_catalog_that_does_not_load(tmp_path, case):
    fake = await _seed(tmp_path)
    if case == "stale-version":
        equip = _equip(tmp_path)
        equip["version"] = 2  # keeps items_game_url
        (tmp_path / "equip.json").write_text(json.dumps(equip), encoding="utf-8")
    else:
        (tmp_path / "schema_items.json").unlink()
    fake.calls.clear()

    report = await _refresh(tmp_path, fake, catalog_if_changed=True)

    assert "schema" in fake.calls
    assert report.catalog_rebuilt is True
    assert len(CatalogService.from_cache(tmp_path)) == 3


async def test_catalog_if_changed_rebuilds_when_the_derivation_changed(tmp_path, monkeypatch):
    fake = await _seed(tmp_path)
    monkeypatch.setattr("tf2_loadout.catalog.CATALOG_DERIVATION", CATALOG_DERIVATION + 1)
    fake.calls.clear()

    report = await _refresh(tmp_path, fake, catalog_if_changed=True)

    assert "schema" in fake.calls
    assert report.catalog_rebuilt is True
    assert _equip(tmp_path)["derivation"] == CATALOG_DERIVATION + 1


async def test_a_staged_file_that_does_not_load_fails_validation(tmp_path, monkeypatch):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    original = PricingService.from_cache

    def refuse_staged(cls, cache_dir):
        if str(cache_dir).replace("\\", "/").rsplit("/", 1)[-1].startswith(".refresh-"):
            raise StaleCacheError("staged prices are stale")
        return original(cache_dir)

    monkeypatch.setattr(PricingService, "from_cache", classmethod(refuse_staged))

    with pytest.raises(CacheValidationError, match="staged cache did not load"):
        await _refresh(tmp_path, fake)

    assert _snapshot(tmp_path) == before
    assert _staging_dirs(tmp_path) == []


def _prices_status(status: int) -> FakeUpstream:
    fake = FakeUpstream()
    fake.overrides["prices"] = httpx.Response(status, text="nope")
    return fake


@pytest.mark.parametrize(
    ("route", "response", "expected", "message"),
    [
        (None, None, ExitCode.OK, None),
        ("prices", httpx.Response(500, text="down"), ExitCode.FETCH_FAILED, "HTTP 500"),
        ("overview", httpx.Response(403, text="no"), ExitCode.BAD_KEY, "key rejected"),
        ("prices", httpx.Response(401, text="no"), ExitCode.BAD_KEY, "key rejected"),
        # backpack.tf's real reply to a bad key (probed 2026-11-21): HTTP 403, not 200.
        (
            "prices",
            httpx.Response(
                403,
                json={"response": {"success": 0, "message": "This API key is not valid."}},
            ),
            ExitCode.BAD_KEY,
            "key rejected",
        ),
        (
            "prices",
            httpx.Response(200, json={"response": {"success": 1, "items": {}}}),
            ExitCode.VALIDATION_FAILED,
            "priced items",
        ),
    ],
)
async def test_run_maps_failures_to_exit_codes(tmp_path, route, response, expected, message):
    fake = FakeUpstream()
    if route:
        fake.overrides[route] = response

    code, out, err = await _run(tmp_path, fake)

    assert code == expected
    assert SECRET not in err
    assert SECRET not in out
    if expected == ExitCode.OK:
        assert json.loads(out)["catalog"]["items_game_hash"] == HASH
    else:
        assert message in err
        assert out == ""


async def test_a_failed_replace_reports_a_partial_commit(tmp_path, monkeypatch):
    fake = await _seed(tmp_path)
    real_replace = refresh_module.os.replace

    def flaky_replace(src, dst):
        if str(dst).endswith("equip.json"):
            raise PermissionError("held open")
        return real_replace(src, dst)

    monkeypatch.setattr("tf2_loadout.refresh.os.replace", flaky_replace)

    code, _, err = await _run(tmp_path, fake)

    assert code == ExitCode.INTERNAL_ERROR
    assert "partial commit" in err
    assert "replaced schema_items.json" in err
    assert "still old: equip.json, prices.json" in err
    assert "PermissionError" in err
    assert _staging_dirs(tmp_path) == []


async def test_run_maps_an_unexpected_error_to_internal_error(tmp_path):
    fake = await _seed(tmp_path)
    before = _snapshot(tmp_path)
    # A wearable with no defindex: merge_catalog raises a KeyError, not a fetch error.
    fake.overrides["schema"] = httpx.Response(
        200, json={"result": {"status": 1, "items": [{"item_class": "tf_wearable"}]}}
    )

    code, out, err = await _run(tmp_path, fake)

    assert code == ExitCode.INTERNAL_ERROR
    assert "internal: KeyError" in err
    assert SECRET not in err
    assert out == ""
    assert _snapshot(tmp_path) == before


def test_missing_keys_lists_blank_or_unset_ones():
    assert missing_keys({}) == ["STEAM_API_KEY", "BACKPACK_TF_API_KEY"]
    assert missing_keys({"STEAM_API_KEY": "x", "BACKPACK_TF_API_KEY": "  "}) == [
        "BACKPACK_TF_API_KEY"
    ]
    assert missing_keys({"STEAM_API_KEY": "x", "BACKPACK_TF_API_KEY": "y"}) == []


def test_main_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "--catalog-if-changed" in capsys.readouterr().out


def test_main_without_keys_exits_bad_key_before_building_clients(tmp_path, monkeypatch):
    monkeypatch.setattr("tf2_loadout.refresh.load_env", lambda: None)
    monkeypatch.delenv("STEAM_API_KEY", raising=False)
    monkeypatch.delenv("BACKPACK_TF_API_KEY", raising=False)

    def no_client(*args, **kwargs):
        pytest.fail("a client was built without keys")

    monkeypatch.setattr("tf2_loadout.refresh.SchemaClient", no_client)
    monkeypatch.setattr("tf2_loadout.refresh.PricingClient", no_client)

    assert main(["--cache-dir", str(tmp_path)]) == ExitCode.BAD_KEY
    assert list(tmp_path.iterdir()) == []
