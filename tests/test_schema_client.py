"""Behavior of the Steam item-schema HTTP client.

GetSchemaItems is paginated: each response carries a page of ``items`` and, when more
remain, a ``next`` start index. The client must follow ``next`` until exhausted and
return the concatenated items.
"""

import json

import httpx
import pytest

from tf2_loadout.schema_client import SchemaClient, SchemaError, items_game_hash


def _page(items, next_start=None):
    result = {"status": 1, "items": items}
    if next_start is not None:
        result["next"] = next_start
    return httpx.Response(200, content=json.dumps({"result": result}))


async def test_follows_pagination_and_concatenates_items():
    pages = [
        _page([{"defindex": 1}, {"defindex": 2}], next_start=2),
        _page([{"defindex": 3}]),  # no "next" -> last page
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("start"))
        return pages[len(calls) - 1]

    transport = httpx.MockTransport(handler)
    async with SchemaClient(api_key="dummy", transport=transport) as client:
        items = await client.fetch_all_items()

    assert [i["defindex"] for i in items] == [1, 2, 3]
    # First call has no start; second call follows next=2.
    assert calls == [None, "2"]


ITEMS_GAME_URL = (
    "http://media.steampowered.com/apps/440/scripts/items/"
    "items_game.4619d9a62858cc1211292c217c50561d11cfefff.txt"
)


def _overview(url=ITEMS_GAME_URL):
    result = {"status": 1}
    if url is not None:
        result["items_game_url"] = url
    return httpx.Response(200, content=json.dumps({"result": result}))


def _items_game_client(download: httpx.Response) -> SchemaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "GetSchemaOverview" in request.url.path:
            return _overview()
        return download

    return SchemaClient(api_key="dummy", transport=httpx.MockTransport(handler))


async def test_items_game_download_error_raises():
    page = httpx.Response(502, text="<html><body>502 Bad Gateway</body></html>")
    async with _items_game_client(page) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_items_game()


async def test_items_game_html_with_200_raises_schema_error():
    page = httpx.Response(200, text="<html><body>502 Bad Gateway</body></html>")
    async with _items_game_client(page) as client:
        with pytest.raises(SchemaError, match="items_game"):
            await client.fetch_items_game()


async def test_truncated_items_game_raises_schema_error():
    page = httpx.Response(200, text='"items_game"\n{\n\t"items"\n\t{\n\t\t"1"\n\t\t{')
    async with _items_game_client(page) as client:
        with pytest.raises(SchemaError, match="VDF"):
            await client.fetch_items_game()


async def test_fetch_items_game_url_returns_overview_url():
    transport = httpx.MockTransport(lambda request: _overview())
    async with SchemaClient(api_key="dummy", transport=transport) as client:
        assert await client.fetch_items_game_url() == ITEMS_GAME_URL


async def test_overview_without_url_raises():
    transport = httpx.MockTransport(lambda request: _overview(url=None))
    async with SchemaClient(api_key="dummy", transport=transport) as client:
        with pytest.raises(SchemaError, match="items_game_url"):
            await client.fetch_items_game_url()


async def test_non_json_schema_page_raises_schema_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>oops</html>"))
    async with SchemaClient(api_key="dummy", transport=transport) as client:
        with pytest.raises(SchemaError, match="non-JSON"):
            await client.fetch_all_items()


def test_items_game_hash_extracts_sha():
    assert items_game_hash(ITEMS_GAME_URL) == "4619d9a62858cc1211292c217c50561d11cfefff"


def test_items_game_hash_is_none_for_an_unexpected_url():
    assert items_game_hash("http://media.steampowered.com/apps/440/items_game.txt") is None


@pytest.mark.parametrize("body", ["null", "[]", '{"result": null}'])
async def test_a_json_body_that_is_not_an_object_raises_schema_error(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with SchemaClient(api_key="dummy", transport=transport) as client:
        with pytest.raises(SchemaError):
            await client.fetch_all_items()
        with pytest.raises(SchemaError):
            await client.fetch_items_game_url()
