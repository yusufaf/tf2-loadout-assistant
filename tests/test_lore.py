"""Behavior of LoreService — item descriptions sourced from the TF2 wiki.

It depends only on an async ``client.query(**params)`` (the reused WikiClient), so tests
drive it with a stub. Results are cached on disk to spare the rate-limited wiki.
"""

import os
import time

import pytest
from tf2_wiki_mcp.client import WikiError

from tf2_loadout.lore import MISS_TTL_SECONDS, LoreService, ItemLore


class RaisingClient:
    """A client whose every query fails, as WikiClient does for an API error."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    async def query(self, **params):
        self.calls += 1
        raise self.exc


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
# A page that exists but has no usable lead (e.g. a stub). A page that doesn't exist at
# all raises WikiError("missingtitle: ...") instead -- see RaisingClient.
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


@pytest.mark.parametrize("junk", ["{not json", '["x"]', '{"bogus": 1}'])
async def test_corrupt_cache_file_is_treated_as_a_miss(tmp_path, junk):
    client = StubClient(PAGE)
    lore = LoreService(client, cache_dir=tmp_path)
    first = await lore.get_lore("Team Captain")
    # Glob rather than a computed name, so this doesn't pin the filename scheme.
    for p in (tmp_path / "lore").glob("*.json"):
        p.write_text(junk, encoding="utf-8")

    second = await lore.get_lore("Team Captain")

    assert second == first
    assert client.calls == 2  # refetched instead of raising
    assert await lore.get_lore("Team Captain") == first
    assert client.calls == 2  # and the rewritten file is a good cache again


def _lore_files(tmp_path):
    return list((tmp_path / "lore").glob("*.json"))


async def test_caches_a_miss_and_skips_the_wiki_next_time(tmp_path):
    client = StubClient(MISSING)
    lore = LoreService(client, cache_dir=tmp_path)

    assert await lore.get_lore("Nope") is None
    assert await lore.get_lore("Nope") is None

    assert client.calls == 1


async def test_a_cached_miss_expires(tmp_path):
    client = StubClient(MISSING)
    lore = LoreService(client, cache_dir=tmp_path)
    await lore.get_lore("Nope")
    old = time.time() - MISS_TTL_SECONDS - 60
    for p in _lore_files(tmp_path):
        os.utime(p, (old, old))

    assert await lore.get_lore("Nope") is None

    assert client.calls == 2  # a newly released item's page may exist by now


@pytest.mark.parametrize("code", ["missingtitle", "invalidtitle"])
async def test_a_missing_wiki_page_is_cached(tmp_path, code):
    # This is how a real miss arrives: the API errors rather than returning empty text.
    client = RaisingClient(WikiError(f"{code}: The page you specified doesn't exist."))
    lore = LoreService(client, cache_dir=tmp_path)

    assert await lore.get_lore("Zzz Nope") is None
    assert await lore.get_lore("Zzz Nope") is None

    assert client.calls == 1
    assert len(_lore_files(tmp_path)) == 1


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("boom"),
        WikiError("maxlag: Waiting for a database server"),
        WikiError("MediaWiki API repeatedly asked us to back off"),
    ],
)
async def test_a_wiki_failure_is_not_cached(tmp_path, exc):
    client = RaisingClient(exc)
    lore = LoreService(client, cache_dir=tmp_path)

    assert await lore.get_lore("Team Captain") is None
    assert await lore.get_lore("Team Captain") is None

    assert client.calls == 2  # an outage must not be pinned for a day
    assert _lore_files(tmp_path) == []


class _PerPageClient:
    """Answers each page with its own distinct lead, counting queries."""

    def __init__(self):
        self.calls = 0

    async def query(self, **params):
        self.calls += 1
        page = params["page"]
        return {
            "parse": {
                "title": page,
                "wikitext": f"'''{page}''' is a distinct cosmetic item used only in this test case.",
            }
        }


async def test_names_that_slug_alike_get_separate_cache_entries(tmp_path):
    client = _PerPageClient()
    lore = LoreService(client, cache_dir=tmp_path)

    a = await lore.get_lore("Hat!")
    b = await lore.get_lore("Hat?")

    assert a.summary != b.summary
    assert client.calls == 2
    assert await lore.get_lore("Hat!") == a
    assert await lore.get_lore("Hat?") == b
    assert client.calls == 2
