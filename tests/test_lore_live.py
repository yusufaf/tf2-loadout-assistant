"""Live check that LoreService reads real summaries from the TF2 wiki.

Skipped unless ``--live`` is passed.
"""

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
