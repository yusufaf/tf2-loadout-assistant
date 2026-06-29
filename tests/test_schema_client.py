"""Behavior of the Steam item-schema HTTP client.

GetSchemaItems is paginated: each response carries a page of ``items`` and, when more
remain, a ``next`` start index. The client must follow ``next`` until exhausted and
return the concatenated items.
"""

import json

import httpx
import pytest

from tf2_loadout.schema_client import SchemaClient


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
