"""Item lore from the TF2 wiki, reusing tf2-wiki-mcp's WikiClient.

Provides the lead-section description for a cosmetic, which feeds style reasoning. The
wiki is rate-limited, so results are cached on disk -- misses included (for a day), so
an unknown item doesn't hit the wiki on every request. Only "no such page" and "page
has no lead" count as misses; a transient failure is never cached.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from tf2_wiki_mcp.client import WikiError

# Pages for newly released items appear on the wiki later, so a negative entry must
# expire rather than hide them forever.
MISS_TTL_SECONDS = 24 * 3600

# MediaWiki error codes meaning "no page by this name", as opposed to an outage or
# back-off. WikiClient only exposes the code as the "<code>: <info>" message prefix.
_NO_PAGE_CODES = frozenset({"missingtitle", "invalidtitle"})


@dataclass(frozen=True)
class ItemLore:
    name: str
    title: str | None
    summary: str | None


class _Queryable(Protocol):
    async def query(self, **params: object) -> dict: ...


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def _name_digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def _is_missing_page(exc: Exception) -> bool:
    return isinstance(exc, WikiError) and str(exc).partition(":")[0] in _NO_PAGE_CODES


class LoreService:
    def __init__(self, client: _Queryable, cache_dir: str | Path | None = None):
        self._client = client
        self._cache_dir = Path(cache_dir) if cache_dir else None

    def _cache_file(self, name: str) -> Path | None:
        if self._cache_dir is None:
            return None
        # The slug alone is lossy ("Hat!" and "Hat?" both become "hat"), so the digest
        # of the exact name keeps it readable but unique.
        return self._cache_dir / "lore" / f"{_slug(name)}-{_name_digest(name)}.json"

    async def get_lore(self, item_name: str) -> ItemLore | None:
        cache_file = self._cache_file(item_name)
        if cache_file:
            cached = self._read_cache(cache_file)
            if cached is not None:
                return cached if cached.summary else None

        # The TF2 wiki lacks the TextExtracts extension and its rendered infobox
        # duplicates flavor text, so we take the lead sentence from the raw wikitext.
        try:
            data = await self._client.query(
                action="parse", page=item_name, prop="wikitext", redirects=1
            )
        except Exception as exc:
            if not _is_missing_page(exc):
                return None  # transient; deliberately not cached
            data = {}
        parse = data.get("parse", {})
        summary = _lead_from_wikitext(parse.get("wikitext") or "")

        lore = ItemLore(name=item_name, title=parse.get("title"), summary=summary)
        if cache_file:
            self._write_cache(cache_file, lore)
        return lore if summary else None

    def _read_cache(self, path: Path) -> ItemLore | None:
        try:
            lore = ItemLore(**json.loads(path.read_text(encoding="utf-8")))
            age = time.time() - path.stat().st_mtime
        except (OSError, ValueError, TypeError):
            # Missing, unreadable, truncated or wrong-shaped: all just mean "fetch again".
            return None
        if lore.summary is None and age > MISS_TTL_SECONDS:
            return None
        return lore

    def _write_cache(self, path: Path, lore: ItemLore) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(lore)), encoding="utf-8")
        except OSError:
            # The cache is an optimization; failing to write it must not fail the lookup.
            pass


def _lead_from_wikitext(wikitext: str) -> str | None:
    """Extract the lead sentence(s) from page wikitext, stripping markup.

    The lead lives after the ``{{Item infobox}}`` template, typically as
    ``'''Name''' is a ...``. We drop refs/comments/templates/files/links and bold
    markup, then return the first prose line of reasonable length.
    """
    s = wikitext
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S | re.I)
    s = re.sub(r"<ref[^>]*/>", "", s, flags=re.I)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    # remove templates iteratively (handles nesting)
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\{\{[^{}]*\}\}", "", s, flags=re.S)
    s = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", s, flags=re.I)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)  # [[a|b]] -> b
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)  # [[a]] -> a
    s = re.sub(r"'{2,5}", "", s)  # bold / italic
    s = re.sub(r"<[^>]+>", "", s)  # any stray html

    for line in s.splitlines():
        text = line.strip()
        if not text or text[0] in "|{}*:=!#":
            continue
        text = re.sub(r"\s+", " ", text)
        if len(text) >= 40:
            return text
    return None
