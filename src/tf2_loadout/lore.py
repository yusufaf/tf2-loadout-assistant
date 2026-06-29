"""Item lore from the TF2 wiki, reusing tf2-wiki-mcp's WikiClient.

Provides the lead-section description for a cosmetic, which feeds style reasoning. The
wiki is rate-limited, so results are cached on disk.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ItemLore:
    name: str
    title: str | None
    summary: str | None


class _Queryable(Protocol):
    async def query(self, **params: object) -> dict: ...


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


class LoreService:
    def __init__(self, client: _Queryable, cache_dir: str | Path | None = None):
        self._client = client
        self._cache_dir = Path(cache_dir) if cache_dir else None

    def _cache_file(self, name: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / "lore" / f"{_slug(name)}.json"

    async def get_lore(self, item_name: str) -> ItemLore | None:
        cache_file = self._cache_file(item_name)
        if cache_file and cache_file.exists():
            return ItemLore(**json.loads(cache_file.read_text(encoding="utf-8")))

        # The TF2 wiki lacks the TextExtracts extension and its rendered infobox
        # duplicates flavor text, so we take the lead sentence from the raw wikitext.
        try:
            data = await self._client.query(
                action="parse", page=item_name, prop="wikitext", redirects=1
            )
        except Exception:
            return None
        parse = data.get("parse", {})
        summary = _lead_from_wikitext(parse.get("wikitext") or "")
        if not summary:
            return None

        lore = ItemLore(name=item_name, title=parse.get("title"), summary=summary)
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(asdict(lore)), encoding="utf-8")
        return lore


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
