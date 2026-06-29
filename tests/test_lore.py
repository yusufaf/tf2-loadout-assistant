"""Behavior of LoreService — item descriptions sourced from the TF2 wiki.

It depends only on an async ``client.query(**params)`` (the reused WikiClient), so tests
drive it with a stub. Results are cached on disk to spare the rate-limited wiki.
"""

import pytest

from tf2_loadout.lore import LoreService, ItemLore


class StubClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0
        self.last_params: dict = {}

    async def query(self, **params):
        self.calls += 1
        self.last_params = params
        return self.response


PAGE = {
    "parse": {
        "title": "Team Captain",
        "wikitext": (
            "{{Item infobox\n| name = Team Captain\n| image = tc.png\n}}\n"
            "[[File:tc.png|thumb]]\n"
            "'''The Team Captain''' is a community-created [[cosmetic item]] for all "
            "[[classes]].<ref>Contributed</ref>\n\n== Painting ==\nText."
        ),
    }
}
# action=parse on a missing page yields no usable wikitext.
MISSING = {"parse": {"title": "Nope", "wikitext": ""}}


async def test_returns_summary_for_a_known_item():
    client = StubClient(PAGE)
    lore = LoreService(client)

    result = await lore.get_lore("Team Captain")

    assert isinstance(result, ItemLore)
    assert result.title == "Team Captain"
    assert result.summary.startswith("The Team Captain is a community-created")
    assert "cosmetic item" in result.summary  # link markup stripped to text
    assert "<ref>" not in result.summary and "{{" not in result.summary
    # read raw wikitext (no TextExtracts on the TF2 wiki), following redirects
    assert client.last_params["action"] == "parse"
    assert client.last_params["prop"] == "wikitext"
    assert client.last_params["redirects"] == 1


async def test_missing_page_returns_none():
    assert await LoreService(StubClient(MISSING)).get_lore("Nope") is None


async def test_caches_to_disk_and_avoids_second_fetch(tmp_path):
    client = StubClient(PAGE)
    lore = LoreService(client, cache_dir=tmp_path)

    first = await lore.get_lore("Team Captain")
    second = await lore.get_lore("Team Captain")

    assert first == second
    assert client.calls == 1  # second call served from cache
