"""HTTP behavior of the rate limits and the Origin check, wired through create_app.

The limiter math is ``test_ratelimit.py``'s job; this file is about which routes are
limited, what they key on, and that ``limits=None`` (every other test) means off.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, ToolCallPart

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_agent
from tf2_loadout.api import create_app
from tf2_loadout.auth import SignedInUser
from tf2_loadout.catalog import CatalogService
from tf2_loadout.inventory import InventoryResult
from tf2_loadout.lore import LoreService
from tf2_loadout.models import Cosmetic
from tf2_loadout.pricing import PricingService
from tf2_loadout.ratelimit import RateLimiter, RateLimits

LLM_HEADERS = {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": "sk-test"}
STEAM_ID = "76561197960287930"
FEDORA = Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")


def _model_fn(messages, info):
    return ModelResponse(
        parts=[ToolCallPart(info.output_tools[0].name, {"message": "Try the fedora."})]
    )


class _StubWiki:
    async def query(self, **params):
        return {
            "parse": {
                "title": "Spy Fedora",
                "wikitext": "'''The Spy Fedora''' is a dapper cosmetic item for the Spy.",
            }
        }


class _StubAuth:
    async def complete_login(self, params):
        return SignedInUser(steam_id=STEAM_ID, persona="Spy", avatar=None, profile_url=None)

    def login_url(self, state: str) -> str:
        return f"https://steamcommunity.com/openid/login?state={state}"


class _StubInventory:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def fetch(self, steam_id: str, *, refresh: bool = False) -> InventoryResult:
        self.calls.append(refresh)
        return InventoryResult("ok", frozenset(), 0.0)


def _client(limits: RateLimits | None = None, **kwargs) -> TestClient:
    catalog = CatalogService([FEDORA])
    pricing = PricingService({})
    model = FunctionModel(_model_fn)
    chat = LoadoutAgentService(
        build_agent(None),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
        model_factory=lambda llm: model,
    )
    lore = LoreService(_StubWiki())
    return TestClient(create_app(catalog, pricing, lore, chat, limits=limits, **kwargs))


def _signed_in_client(**kwargs) -> TestClient:
    client = _client(auth=_StubAuth(), session_secret="test-secret", https_only=False, **kwargs)
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )
    return client


def _turn(client: TestClient, path: str = "/chat", headers=LLM_HEADERS, **extra):
    return client.post(path, json={"message": "hi"}, headers={**headers, **extra})


def test_chat_429s_past_the_burst_with_retry_after():
    client = _client(RateLimits(chat=RateLimiter(2, 60)))
    assert [_turn(client).status_code for _ in range(2)] == [200, 200]
    r = _turn(client)
    assert r.status_code == 429
    assert r.json() == {"detail": "too many requests"}
    assert int(r.headers["retry-after"]) > 0


def test_chat_and_chat_stream_share_one_bucket():
    client = _client(RateLimits(chat=RateLimiter(2, 60)))
    assert _turn(client, "/chat").status_code == 200
    assert _turn(client, "/chat/stream").status_code == 200
    assert _turn(client, "/chat/stream").status_code == 429
    assert _turn(client, "/chat").status_code == 429


def test_chat_throttle_counts_requests_without_a_key():
    # The 401-vs-200 answer is the key-testing oracle, so probing it spends tokens too.
    client = _client(RateLimits(chat=RateLimiter(2, 60)))
    assert [_turn(client, headers={}).status_code for _ in range(3)] == [401, 401, 429]


def test_chat_buckets_are_per_client_ip():
    client = _client(RateLimits(chat=RateLimiter(1, 60)), client_ip_header="fly-client-ip")
    assert _turn(client, **{"fly-client-ip": "1.1.1.1"}).status_code == 200
    assert _turn(client, **{"fly-client-ip": "1.1.1.1"}).status_code == 429
    assert _turn(client, **{"fly-client-ip": "2.2.2.2"}).status_code == 200


def test_chat_ignores_a_client_ip_header_it_was_not_told_to_trust():
    client = _client(RateLimits(chat=RateLimiter(1, 60)))
    assert _turn(client, **{"fly-client-ip": "1.1.1.1"}).status_code == 200
    assert _turn(client, **{"fly-client-ip": "2.2.2.2"}).status_code == 429


def test_no_limits_means_no_throttling():
    client = _client(None)
    assert [_turn(client).status_code for _ in range(5)] == [200] * 5


def test_lore_429s_past_the_burst():
    client = _client(RateLimits(lore=RateLimiter(2, 60)))
    assert [client.get("/lore/1").status_code for _ in range(3)] == [200, 200, 429]


def test_inventory_refresh_is_throttled_per_steam_id():
    stub = _StubInventory()
    client = _signed_in_client(
        limits=RateLimits(inventory_refresh=RateLimiter(1, 60)), inventory=stub
    )
    assert client.get("/me/inventory", params={"refresh": 1}).status_code == 200
    assert client.get("/me/inventory", params={"refresh": 1}).status_code == 429
    assert client.get("/me/inventory").status_code == 200
    assert stub.calls == [True, False]  # the refused refresh never reached Steam


FOREIGN = {"Origin": "https://analytics.yusufaf.dev"}


def test_cross_origin_logout_is_403():
    client = _signed_in_client(public_base_url="https://tf2.yusufaf.dev")
    r = client.post("/auth/logout", headers=FOREIGN)
    assert r.status_code == 403
    assert r.json() == {"detail": "cross-origin request refused"}
    assert client.get("/auth/me").json()["signed_in"] is True  # session untouched


def test_cross_origin_loadout_write_is_403():
    client = _signed_in_client(public_base_url="https://tf2.yusufaf.dev")
    body = {"name": "Spy", "cls": "Spy", "defindexes": [1]}
    assert client.post("/me/loadouts", json=body, headers=FOREIGN).status_code == 403
    assert client.delete("/me/loadouts/abc", headers=FOREIGN).status_code == 403


def _conflicts(client: TestClient, origin: str) -> int:
    return client.post(
        "/loadout/conflicts", json={"defindexes": [1]}, headers={"Origin": origin}
    ).status_code


def test_same_origin_and_public_origin_writes_pass():
    client = _signed_in_client(public_base_url="https://tf2.yusufaf.dev")
    for origin in ("http://testserver", "https://tf2.yusufaf.dev"):
        assert _conflicts(client, origin) == 200, origin
    assert client.post("/auth/logout", headers={"Origin": "http://testserver"}).status_code == 204


def test_dev_origins_are_allowed_only_off_an_https_deployment():
    assert _conflicts(_client(), "http://localhost:5173") == 200
    assert _conflicts(_client(public_base_url="http://localhost:8000"), "http://127.0.0.1:5173") == 200
    prod = _client(public_base_url="https://tf2.yusufaf.dev")
    assert _conflicts(prod, "http://localhost:5173") == 403
