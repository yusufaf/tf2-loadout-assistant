"""HTTP behavior of the loadout API.

The app is built from a catalog + pricing service so tests can inject small fixtures.
Cosmetic responses join in the price when one exists.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_agent
from tf2_loadout.models import Cosmetic, Price
from tf2_loadout.catalog import CatalogService
from tf2_loadout.lore import ItemLore, LoreService
from tf2_loadout.pricing import PricingService
from pydantic_core import to_jsonable_python

from tf2_loadout.api import (
    MAX_COSMETIC_LOOKUP,
    MAX_HISTORY_CHARS,
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_CHARS,
    CanonicalHostRedirect,
    create_app,
    fit_history,
)
from tf2_loadout.config import PROVIDERS
from tf2_loadout.conflicts import MAX_EQUIPPED

# What the browser sends with every chat turn. The value is never checked here --
# the stub factory below ignores it -- but its absence must be a 401.
LLM_HEADERS = {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": "sk-test"}
PUBLIC_BASE_URL = "https://tf2.yusufaf.dev"


def _service(model: Model, deps: LoadoutDeps) -> LoadoutAgentService:
    """Production shape (an agent with no model of its own) with a canned model
    standing in for whatever the player's key would have built."""
    return LoadoutAgentService(build_agent(None), deps, model_factory=lambda llm: model)


class _StubWiki:
    async def query(self, **params):
        return {
            "parse": {
                "title": "Spy Fedora",
                "wikitext": "'''The Spy Fedora''' is a dapper cosmetic item for the Spy.",
            }
        }


def _client(public_base_url: str | None = None) -> TestClient:
    catalog = CatalogService(
        [
            Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1"),
            Cosmetic(2, "Spy Shades", frozenset({"glasses"}), ("Spy",), "misc", "img2"),
            Cosmetic(3, "Scout Cap", frozenset({"hat"}), ("Scout",), "misc", "img3"),
        ]
    )
    pricing = PricingService({1: Price("metal", 5.0, 6.0)})
    lore = LoreService(_StubWiki())
    return TestClient(create_app(catalog, pricing, lore, public_base_url=public_base_url))


def test_healthz_reports_counts():
    r = _client().get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cosmetics"] == 3
    assert body["priced"] == 1


def test_cosmetics_filtered_by_class_join_prices():
    r = _client().get("/cosmetics", params={"used_by": "Spy"})
    assert r.status_code == 200
    items = {c["defindex"]: c for c in r.json()["items"]}
    assert set(items) == {1, 2}
    assert items[1]["price"] == {
        "currency": "metal",
        "value": 5.0,
        "value_high": 6.0,
        "ref_value": 5.0,
    }
    assert items[2]["price"] is None  # no price known


def test_get_single_cosmetic_and_404():
    client = _client()
    assert client.get("/cosmetics/3").json()["name"] == "Scout Cap"
    assert client.get("/cosmetics/999").status_code == 404


def test_lore_endpoint_returns_summary():
    r = _client().get("/lore/1")
    assert r.status_code == 200
    assert "dapper cosmetic item for the Spy" in r.json()["summary"]


def test_lore_endpoint_survives_a_corrupt_cache_file(tmp_path):
    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    lore = LoreService(_StubWiki(), cache_dir=tmp_path)
    client = TestClient(create_app(catalog, PricingService({}), lore))
    assert client.get("/lore/1").status_code == 200  # populates the cache
    for p in (tmp_path / "lore").glob("*.json"):
        p.write_text("{not json", encoding="utf-8")

    r = client.get("/lore/1")

    assert r.status_code == 200
    assert "dapper cosmetic item for the Spy" in r.json()["summary"]


def _chat_client() -> TestClient:
    """A client whose agent always answers with a fixed structured reply."""

    def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "Try the fedora.", "suggested_defindexes": [1]},
                )
            ]
        )

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = _service(
        FunctionModel(model_fn),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    return TestClient(create_app(catalog, pricing, None, chat))


def test_chat_returns_503_when_no_agent_configured():
    r = _client().post("/chat", json={"message": "dress my Spy"})
    assert r.status_code == 503
    assert "chat" in r.json()["detail"].lower()


def test_healthz_reports_chat_availability():
    assert _client().get("/healthz").json()["chat"] is False
    assert _chat_client().get("/healthz").json()["chat"] is True


def test_chat_returns_message_and_suggestions():
    r = _chat_client().post("/chat", json={"message": "dress my Spy"}, headers=LLM_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "Try the fedora."
    assert body["suggested_defindexes"] == [1]
    assert body["conflicts"] == []
    assert body["history"]


def test_chat_reports_conflicts_in_the_suggested_set():
    """A lazy model can skip check_conflicts; the server must catch it anyway."""

    def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    # 1 and 3 both occupy "hat".
                    {"message": "Two hats!", "suggested_defindexes": [1, 3]},
                )
            ]
        )

    catalog = CatalogService(
        [
            Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1"),
            Cosmetic(3, "Scout Cap", frozenset({"hat"}), ("Scout",), "misc", "img3"),
        ]
    )
    pricing = PricingService({})
    chat = _service(
        FunctionModel(model_fn),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    client = TestClient(create_app(catalog, pricing, None, chat))
    r = client.post("/chat", json={"message": "give me two hats"}, headers=LLM_HEADERS)
    body = r.json()
    assert body["suggested_defindexes"] == [1, 3]
    assert len(body["conflicts"]) == 1
    assert {body["conflicts"][0]["a"], body["conflicts"][0]["b"]} == {1, 3}


def test_chat_forwards_equipped_to_the_agent():
    """The agent must see the tray, not just bare names in the prompt."""

    def model_fn(messages, info):
        # The dynamic instruction renders equipped defindexes by name; echo one back
        # so the test can tell the agent actually received it.
        instructions = next(
            (m.instructions for m in messages if getattr(m, "instructions", None)),
            "",
        )
        assert "1: Spy Fedora" in instructions
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "ok", "suggested_defindexes": []},
                )
            ]
        )

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = _service(
        FunctionModel(model_fn),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    client = TestClient(create_app(catalog, pricing, None, chat))
    r = client.post("/chat", json={"message": "hi", "equipped": [1]}, headers=LLM_HEADERS)
    assert r.status_code == 200


def test_chat_rejects_oversized_equipped_list():
    client = _chat_client()
    r = client.post("/chat", json={"message": "hi", "equipped": list(range(50))}, headers=LLM_HEADERS)
    assert r.status_code == 422


def test_chat_drops_defindexes_that_are_not_in_the_catalog():
    """Weaker models name items they never looked up; the API must not pass them on."""

    def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    # 1 is real, 999 is invented.
                    {"message": "Try these.", "suggested_defindexes": [1, 999]},
                )
            ]
        )

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = _service(
        FunctionModel(model_fn),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    client = TestClient(create_app(catalog, pricing, None, chat))
    r = client.post("/chat", json={"message": "dress my Spy"}, headers=LLM_HEADERS)
    assert r.json()["suggested_defindexes"] == [1]


def _stream_lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_chat_stream_returns_503_when_no_agent_configured():
    r = _client().post("/chat/stream", json={"message": "dress my Spy"})
    assert r.status_code == 503


def test_chat_stream_emits_tool_progress_then_a_final_line():
    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = _service(
        TestModel(
            custom_output_args={
                "message": "Try the fedora.",
                # 999 is invented; the stream must drop it like /chat does.
                "suggested_defindexes": [1, 999],
            }
        ),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    client = TestClient(create_app(catalog, pricing, None, chat))

    r = client.post("/chat/stream", json={"message": "dress my Spy"}, headers=LLM_HEADERS)
    assert r.status_code == 200

    lines = _stream_lines(r)
    assert [line["kind"] for line in lines[:-1]] == ["tool"] * (len(lines) - 1)
    final = lines[-1]
    assert final["kind"] == "final"
    assert final["message"] == "Try the fedora."
    assert final["suggested_defindexes"] == [1]
    assert final["conflicts"] == []
    assert final["history"]


def test_chat_history_round_trips():
    client = _chat_client()
    first = client.post("/chat", json={"message": "dress my Spy"}, headers=LLM_HEADERS).json()
    second = client.post(
        "/chat",
        json={"message": "and a hat?", "history": first["history"]},
        headers=LLM_HEADERS,
    )
    assert second.status_code == 200
    assert len(second.json()["history"]) > len(first["history"])


def test_chat_rejects_oversized_history():
    client = _chat_client()
    r = client.post("/chat", json={"message": "hi", "history": [{}] * 200}, headers=LLM_HEADERS)
    assert r.status_code == 422


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_chat_rejects_oversized_message(route):
    r = _chat_client().post(
        route, json={"message": "x" * (MAX_MESSAGE_CHARS + 1)}, headers=LLM_HEADERS
    )
    assert r.status_code == 422


def test_chat_rejects_oversized_history_entry():
    client = _chat_client()
    history = client.post(
        "/chat", json={"message": "dress my Spy"}, headers=LLM_HEADERS
    ).json()["history"]
    # A valid-shaped history, so this fails on size alone (not "malformed history").
    prompt = history[0]["parts"][0]
    assert prompt["part_kind"] == "user-prompt"
    prompt["content"] = "x" * MAX_HISTORY_CHARS

    r = client.post(
        "/chat", json={"message": "and a hat?", "history": history}, headers=LLM_HEADERS
    )

    assert r.status_code == 422


def _turn(n: int, calls: int, carry_return: bool = False) -> list:
    """One turn: a user prompt, then ``calls`` tool call/return pairs."""
    first_parts = [UserPromptPart(f"turn {n}")]
    if carry_return:
        # pydantic-ai can merge the previous turn's final tool return into this request.
        first_parts.insert(0, ToolReturnPart("final_result", "ok", tool_call_id=f"f{n - 1}"))
    msgs = [ModelRequest(parts=first_parts)]
    for k in range(calls):
        msgs.append(
            ModelResponse(
                parts=[ToolCallPart("search_cosmetics", {"used_by": "Spy"}, tool_call_id=f"c{n}-{k}")]
            )
        )
        msgs.append(
            ModelRequest(parts=[ToolReturnPart("search_cosmetics", [], tool_call_id=f"c{n}-{k}")])
        )
    return msgs


def _size(messages) -> int:
    return sum(len(json.dumps(to_jsonable_python(m))) for m in messages)


def test_fit_history_keeps_newest_whole_turns():
    t0, t1, t2 = (_turn(i, 3) for i in range(3))  # 7 messages each
    assert fit_history(t0 + t1 + t2, max_messages=14, max_chars=10**9) == t1 + t2


def test_fit_history_respects_the_char_cap():
    t0, t1, t2 = (_turn(i, 3) for i in range(3))
    assert fit_history(t0 + t1 + t2, max_messages=100, max_chars=_size(t2)) == t2


def test_fit_history_returns_nothing_when_the_newest_turn_is_too_big():
    # The next turn then starts fresh instead of 422ing on a transcript it can't resend.
    assert fit_history(_turn(0, 3), max_messages=2, max_chars=10**9) == []


def test_fit_history_drops_a_carried_tool_return_from_the_first_kept_request():
    t0, t1, t2 = _turn(0, 3), _turn(1, 3, carry_return=True), _turn(2, 3)
    kept = fit_history(t0 + t1 + t2, max_messages=14, max_chars=10**9)
    # Its call was trimmed away with the previous turn, so the return would be an orphan.
    assert [p.part_kind for p in kept[0].parts] == ["user-prompt"]
    assert kept[1:] == (t1 + t2)[1:]


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_chat_history_stays_within_caps_across_many_turns(route):
    """A real turn makes several tool calls; resending the returned history every turn
    must never trip the history caps."""

    def model_fn(messages, info):
        calls = 0
        for m in reversed(messages):
            if m.kind == "request" and any(p.part_kind == "user-prompt" for p in m.parts):
                break
            if m.kind == "response":
                calls += 1
        if calls < 7:
            return ModelResponse(parts=[ToolCallPart("search_cosmetics", {"used_by": "Spy"})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "ok", "suggested_defindexes": [1]},
                )
            ]
        )

    # /chat/stream runs the agent with an event stream handler, so the model must also
    # be streamable: replay model_fn's one tool call as a single delta.
    async def stream_fn(messages, info):
        (part,) = model_fn(messages, info).parts
        yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))}

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = _service(
        FunctionModel(model_fn, stream_function=stream_fn),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
    )
    client = TestClient(create_app(catalog, pricing, None, chat))

    history: list[dict] = []
    for i in range(5):
        r = client.post(
            route, json={"message": f"turn {i}", "history": history}, headers=LLM_HEADERS
        )
        assert r.status_code == 200, (i, r.text[:200])
        if route == "/chat":
            body = r.json()
        else:
            body = next((e for e in _stream_lines(r) if e.get("kind") == "final"), None)
            assert body is not None, (i, _stream_lines(r)[-1:])
        history = body["history"]
        assert 0 < len(history) <= MAX_HISTORY_MESSAGES
        assert any(p["part_kind"] == "user-prompt" for p in history[0]["parts"])


def test_cosmetics_lookup_returns_found_in_order_and_missing():
    r = _client().post("/cosmetics/lookup", json={"defindexes": [3, 999, 1, 3]})
    assert r.status_code == 200
    body = r.json()
    assert [c["defindex"] for c in body["items"]] == [3, 1]
    assert body["missing"] == [999]
    assert body["items"][1]["price"]["value"] == 5.0


def test_cosmetics_lookup_caps_request_size():
    r = _client().post(
        "/cosmetics/lookup", json={"defindexes": [1] * (MAX_COSMETIC_LOOKUP + 1)}
    )
    assert r.status_code == 422


def test_conflicts_endpoint_flags_same_region():
    # defindex 1 and 3 both occupy "hat".
    r = _client().post("/loadout/conflicts", json={"defindexes": [1, 3]})
    assert r.status_code == 200
    conflicts = r.json()["conflicts"]
    assert len(conflicts) == 1
    assert {conflicts[0]["a"], conflicts[0]["b"]} == {1, 3}
    assert "hat" in conflicts[0]["regions"]


def test_conflicts_endpoint_rejects_more_than_max_equipped():
    # The pairwise check is quadratic, so the list is capped rather than trusted.
    r = _client().post(
        "/loadout/conflicts", json={"defindexes": [1] * (MAX_EQUIPPED + 1)}
    )
    assert r.status_code == 422


def test_conflicts_endpoint_ignores_duplicate_defindexes():
    r = _client().post("/loadout/conflicts", json={"defindexes": [1, 1, 3]})
    assert r.status_code == 200
    conflicts = r.json()["conflicts"]
    # One pair, not an item "conflicting" with itself.
    assert len(conflicts) == 1
    assert {conflicts[0]["a"], conflicts[0]["b"]} == {1, 3}


def test_chat_caps_suggestions_at_max_equipped():
    ids = list(range(1, MAX_EQUIPPED + 5))

    def model_fn(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "Everything!", "suggested_defindexes": ids + [ids[0]]},
                )
            ]
        )

    # Each item has its own region, so none of them conflict.
    catalog = CatalogService(
        [
            Cosmetic(i, f"Item {i}", frozenset({f"r{i}"}), ("Spy",), "misc", f"img{i}")
            for i in ids
        ]
    )
    pricing = PricingService({})
    chat = _service(
        FunctionModel(model_fn), LoadoutDeps(catalog=catalog, pricing=pricing, lore=None)
    )
    client = TestClient(create_app(catalog, pricing, None, chat))

    body = client.post("/chat", json={"message": "hi"}, headers=LLM_HEADERS).json()

    assert body["suggested_defindexes"] == ids[:MAX_EQUIPPED]
    assert body["conflicts"] == []


# --- filter surfaces: attributes on the payload, the matrix, and uncapped lists ---


def _matrix_client() -> TestClient:
    """A client whose catalog carries a conflict matrix and a paintable, styled item."""
    catalog = CatalogService(
        [
            Cosmetic(
                1,
                "Spy Fedora",
                frozenset({"hat"}),
                ("Spy",),
                "misc",
                "img1",
                paintable=True,
                styles=("Default", "Rogue"),
            ),
            Cosmetic(2, "Spy Mask", frozenset({"whole_head"}), ("Spy",), "misc", "img2"),
        ],
        conflict_matrix={
            "whole_head": frozenset({"hat"}),
            "hat": frozenset({"whole_head"}),
        },
    )
    return TestClient(create_app(catalog, PricingService({})))


def test_cosmetic_payload_includes_filter_attributes():
    r = _matrix_client().get("/cosmetics")

    assert r.status_code == 200
    items = {c["defindex"]: c for c in r.json()["items"]}
    assert items[1]["paintable"] is True
    assert items[1]["styles"] == ["Default", "Rogue"]
    assert items[1]["holiday_restriction"] is None
    assert items[2]["paintable"] is False
    assert items[2]["styles"] == []


def test_equip_conflicts_returns_a_sorted_symmetric_matrix():
    r = _matrix_client().get("/equip-conflicts")

    assert r.status_code == 200
    assert r.json()["matrix"] == {"hat": ["whole_head"], "whole_head": ["hat"]}


def test_limit_zero_returns_everything():
    client = _client()

    everything = client.get("/cosmetics", params={"limit": 0}).json()["items"]
    capped = client.get("/cosmetics", params={"limit": 1}).json()["items"]

    assert len(capped) == 1
    assert len(everything) == 3


def test_chat_requires_the_players_key():
    client = _chat_client()
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 401
    assert r.json()["detail"] == "llm key required"

    r = client.post("/chat/stream", json={"message": "hi"})
    assert r.status_code == 401

    # Provider without a key, and key without a provider, are both incomplete.
    r = client.post(
        "/chat", json={"message": "hi"}, headers={"X-LLM-Provider": "anthropic"}
    )
    assert r.status_code == 401
    r = client.post("/chat", json={"message": "hi"}, headers={"X-LLM-API-Key": "sk"})
    assert r.status_code == 401


def test_chat_rejects_a_provider_outside_the_allowlist():
    r = _chat_client().post(
        "/chat",
        json={"message": "hi"},
        headers={"X-LLM-Provider": "ollama", "X-LLM-API-Key": "sk"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "unknown llm provider"


def _failing_client(make_exc) -> TestClient:
    """A client whose model factory raises ``make_exc(llm)`` every turn."""

    def factory(llm):
        raise make_exc(llm)

    catalog = CatalogService(
        [Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")]
    )
    pricing = PricingService({})
    chat = LoadoutAgentService(
        build_agent(None),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
        model_factory=factory,
    )
    return TestClient(create_app(catalog, pricing, None, chat))


def _rejecting_client() -> TestClient:
    """A client whose provider answers every turn with 401, echoing the key as a real
    provider's error body can."""
    return _failing_client(
        lambda llm: ModelHTTPError(
            status_code=401,
            model_name="m",
            body={"error": f"invalid x-api-key {llm.api_key}"},
        )
    )


# (exception factory, whether its text may appear in our own log). A provider body
# can echo the player's key, so for ModelHTTPError only the status is logged.
_UPSTREAM_ERRORS = [
    (
        lambda llm: ModelHTTPError(
            status_code=400,
            model_name="m",
            body={"error": f"SECRET-INTERNAL bad x-api-key {llm.api_key}"},
        ),
        False,
    ),
    (lambda llm: RuntimeError("SECRET-INTERNAL"), True),
]


def test_chat_maps_a_rejected_key_to_401(caplog):
    r = _rejecting_client().post(
        "/chat", json={"message": "hi"}, headers=LLM_HEADERS
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "provider rejected the API key"
    # The key itself must never echo back, nor reach the server log.
    assert "sk-test" not in r.text
    assert "sk-test" not in caplog.text
    assert "chat turn failed" not in caplog.text


@pytest.mark.parametrize("make_exc, text_logged", _UPSTREAM_ERRORS)
def test_chat_hides_upstream_error_text(make_exc, text_logged, caplog):
    r = _failing_client(make_exc).post(
        "/chat", json={"message": "hi"}, headers=LLM_HEADERS
    )
    assert r.status_code == 502
    assert r.json()["detail"].startswith("model error")
    assert "SECRET" not in r.text
    assert "sk-test" not in r.text
    # Hidden from the player, but the failure is still logged for us.
    assert "chat turn failed" in caplog.text
    assert ("SECRET-INTERNAL" in caplog.text) == text_logged
    assert "sk-test" not in caplog.text


@pytest.mark.parametrize("make_exc, text_logged", _UPSTREAM_ERRORS)
def test_chat_stream_hides_upstream_error_text(make_exc, text_logged, caplog):
    r = _failing_client(make_exc).post(
        "/chat/stream", json={"message": "hi"}, headers=LLM_HEADERS
    )
    assert r.status_code == 200
    lines = _stream_lines(r)
    assert len(lines) == 1
    assert lines[0]["kind"] == "error"
    assert "code" not in lines[0]
    assert "SECRET" not in r.text
    assert "chat turn failed" in caplog.text
    assert ("SECRET-INTERNAL" in caplog.text) == text_logged
    assert "sk-test" not in caplog.text


def test_chat_stream_flags_a_rejected_key_in_band():
    r = _rejecting_client().post(
        "/chat/stream", json={"message": "hi"}, headers=LLM_HEADERS
    )
    assert r.status_code == 200
    lines = _stream_lines(r)
    assert lines == [
        {"kind": "error", "code": "bad_key", "detail": "provider rejected the API key"}
    ]


def test_chat_providers_lists_the_allowlist_even_without_a_service():
    for client in (_client(), _chat_client()):
        r = client.get("/chat/providers")
        assert r.status_code == 200
        providers = r.json()["providers"]
        assert [p["id"] for p in providers] == [spec.id for spec in PROVIDERS]
        assert all(p["label"] and p["model"] for p in providers)


def _fly_client(base_url: str = "https://tf2-loadout-af.fly.dev") -> TestClient:
    return TestClient(_client(public_base_url=PUBLIC_BASE_URL).app, base_url=base_url)


def test_fly_hostname_redirects_to_the_custom_domain_keeping_path_and_query():
    client = _fly_client()
    r = client.get("/cosmetics?used_by=Spy&q=fedora", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "https://tf2.yusufaf.dev/cosmetics?used_by=Spy&q=fedora"
    r = client.get("/lore/a%3Fb", follow_redirects=False)
    assert r.headers["location"] == "https://tf2.yusufaf.dev/lore/a%3Fb"
    assert client.head("/", follow_redirects=False).status_code == 301


def test_fly_hostname_redirects_non_get_with_308_so_method_and_body_survive():
    r = _fly_client().post(
        "/chat?source=share", json={"messages": []}, follow_redirects=False
    )
    assert r.status_code == 308
    assert r.headers["location"] == "https://tf2.yusufaf.dev/chat?source=share"


def test_fly_hostname_redirect_percent_encodes_raw_bytes_exactly_once():
    # httpx escapes non-ASCII before sending, so hand the middleware the raw bytes a
    # lenient server could pass through.
    sent = []

    async def send(message):
        sent.append(message)

    async def unreachable(scope, receive, send):
        raise AssertionError("a redirected request reached the app")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/lore/Über?",
        "raw_path": "/lore/Über%3F".encode(),
        "query_string": "q=ä&r=%20".encode(),
        "headers": [(b"host", b"tf2-loadout-af.fly.dev")],
    }
    asyncio.run(CanonicalHostRedirect(unreachable, PUBLIC_BASE_URL)(scope, None, send))
    assert sent[0]["status"] == 301
    assert dict(sent[0]["headers"])[b"location"] == (
        b"https://tf2.yusufaf.dev/lore/%C3%9Cber%3F?q=%C3%A4&r=%20"
    )


def test_fly_hostname_match_ignores_case_and_port():
    r = _fly_client("http://testserver").get(
        "/", headers={"host": "TF2-Loadout-AF.fly.dev:443"}, follow_redirects=False
    )
    assert r.status_code == 301
    assert r.headers["location"] == "https://tf2.yusufaf.dev/"


def test_canonical_and_internal_hosts_are_not_redirected():
    client = _fly_client("http://testserver")
    # Fly's health checker addresses the machine by its private IP, not a public name.
    for host in ("tf2.yusufaf.dev", "172.19.0.2:8000", "tf2-loadout-af.internal"):
        r = client.get("/healthz", headers={"host": host}, follow_redirects=False)
        assert r.status_code == 200, host
        r = client.get("/cosmetics", headers={"host": host}, follow_redirects=False)
        assert r.status_code == 200, host


def test_healthz_is_never_redirected():
    client = _fly_client()
    r = client.get("/healthz", follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # The app's own answer to /healthz/ depends on whether frontend/dist is mounted (a
    # 404, or the router's same-host slash redirect); either way it stays on this host.
    r = client.get("/healthz/", follow_redirects=False)
    assert not r.headers.get("location", "").startswith(PUBLIC_BASE_URL)


def test_no_redirect_without_a_distinct_public_base_url():
    # Unset (local dev, tests) or pointing at the Fly hostname itself, which would loop.
    for public_base_url in (None, "https://tf2-loadout-af.fly.dev"):
        client = TestClient(
            _client(public_base_url=public_base_url).app,
            base_url="https://tf2-loadout-af.fly.dev",
        )
        assert client.get("/cosmetics", follow_redirects=False).status_code == 200
