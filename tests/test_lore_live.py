"""Live check that LoreService reads real summaries from the TF2 wiki.

Skipped unless ``--live`` is passed.
"""

import json

import pytest

from tf2_wiki_mcp.client import WikiClient

from tf2_loadout.lore import LoreService


@pytest.mark.live
async def test_real_wiki_summary():
    async with WikiClient() as client:
        lore = LoreService(client)
        result = await lore.get_lore("Team Captain")

    assert result is not None
    assert result.summary and "Team Captain" in result.summary


@pytest.mark.live
async def test_real_wiki_missing_page_is_cached(tmp_path):
    # Guards the fixtures: a nonexistent page must arrive as something LoreService
    # caches as a miss, or an unknown item re-hits the wiki on every request.
    async with WikiClient() as client:
        lore = LoreService(client, cache_dir=tmp_path)
        assert await lore.get_lore("Zzz Nonexistent Cosmetic Qq") is None

    files = list((tmp_path / "lore").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["summary"] is None
