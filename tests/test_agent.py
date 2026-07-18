"""Agent wiring: tool schemas and what the tools actually return.

No network: schemas are pinned with TestModel, behavior is driven with FunctionModel.
"""

from __future__ import annotations

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel  # noqa: F401  (used across tests)

from tf2_loadout.agent import (
    LoadoutAgentService,
    LoadoutDeps,
    LoadoutReply,
    MAX_OUTPUT_TOKENS,
    MAX_QUERY_KEYWORDS,
    build_agent,
    build_chat_service,
)
from tf2_loadout.config import LLMSettings
from tf2_loadout.lore import LoreService
from tf2_loadout.catalog import CatalogService
from tf2_loadout.models import Cosmetic, Price
from tf2_loadout.pricing import PricingService


def _deps() -> LoadoutDeps:
    catalog = CatalogService(
        [
            Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1"),
            Cosmetic(2, "Spy Shades", frozenset({"glasses"}), ("Spy",), "misc", "img2"),
            Cosmetic(3, "Scout Cap", frozenset({"hat"}), ("Scout",), "misc", "img3"),
        ]
    )
    pricing = PricingService({1: Price("metal", 5.0, 6.0)})
    return LoadoutDeps(catalog=catalog, pricing=pricing, lore=None)


def _tool_returns(messages: list[ModelMessage], tool_name: str) -> list:
    """Pull the return payloads for a named tool out of a captured run."""
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def _calls_then_finishes(tool_name: str, args: dict):
    """A FunctionModel that calls one tool, then emits a final structured output."""

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(
            isinstance(part, ToolReturnPart) and part.tool_name == tool_name
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {"message": "ok", "suggested_defindexes": [1, 2]},
                    )
                ]
            )
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    return FunctionModel(model_fn)


async def test_agent_exposes_the_expected_tools() -> None:
    agent = build_agent(TestModel())
    model = TestModel()
    with agent.override(model=model):
        await agent.run("hello", deps=_deps())
    names = {t.name for t in model.last_model_request_parameters.function_tools}
    assert names == {
        "search_cosmetics",
        "get_cosmetic",
        "check_conflicts",
        "get_item_lore",
    }


async def test_search_cosmetics_schema_takes_class_and_query() -> None:
    agent = build_agent(TestModel())
    model = TestModel()
    with agent.override(model=model):
        await agent.run("hello", deps=_deps())
    search = next(
        t
        for t in model.last_model_request_parameters.function_tools
        if t.name == "search_cosmetics"
    )
    assert set(search.parameters_json_schema["properties"]) == {
        "used_by",
        "query",
        "limit",
    }


async def test_output_is_a_structured_loadout_reply() -> None:
    agent = build_agent(_calls_then_finishes("search_cosmetics", {"used_by": "Spy"}))
    result = await agent.run("dress my Spy", deps=_deps())
    assert isinstance(result.output, LoadoutReply)
    assert result.output.suggested_defindexes == [1, 2]


async def test_search_filters_by_class() -> None:
    agent = build_agent(_calls_then_finishes("search_cosmetics", {"used_by": "Spy"}))
    with capture_run_messages() as messages:
        await agent.run("dress my Spy", deps=_deps())
    items = _tool_returns(messages, "search_cosmetics")[0]
    assert [i["defindex"] for i in items] == [1, 2]


async def test_search_combines_class_and_query() -> None:
    # The Scout Cap matches "cap" but must not surface for a Spy request; catalog.search
    # alone would return it, so the tool has to compose both filters.
    agent = build_agent(
        _calls_then_finishes("search_cosmetics", {"used_by": "Spy", "query": "cap"})
    )
    with capture_run_messages() as messages:
        await agent.run("spy cap", deps=_deps())
    assert _tool_returns(messages, "search_cosmetics")[0] == []


async def test_search_results_carry_prices() -> None:
    # Without prices here, "find me something under 3 ref" forces a get_cosmetic call
    # per candidate and blows the per-turn request budget.
    agent = build_agent(_calls_then_finishes("search_cosmetics", {"used_by": "Spy"}))
    with capture_run_messages() as messages:
        await agent.run("dress my Spy", deps=_deps())
    items = {i["defindex"]: i for i in _tool_returns(messages, "search_cosmetics")[0]}
    assert items[1]["price"] == {"currency": "metal", "value": 5.0, "value_high": 6.0}
    assert items[2]["price"] is None


async def test_search_matches_any_keyword_in_a_multi_word_query() -> None:
    # Models pass keyword lists, not exact substrings. Requiring the whole string to
    # appear made every such search return [], which sent them brute-forcing instead.
    agent = build_agent(
        _calls_then_finishes(
            "search_cosmetics", {"used_by": "Spy", "query": "cop detective fedora"}
        )
    )
    with capture_run_messages() as messages:
        await agent.run("cop look", deps=_deps())
    items = _tool_returns(messages, "search_cosmetics")[0]
    assert [i["defindex"] for i in items] == [1]


async def test_search_ranks_items_matching_more_keywords_first() -> None:
    agent = build_agent(
        _calls_then_finishes("search_cosmetics", {"query": "spy shades"})
    )
    with capture_run_messages() as messages:
        await agent.run("shades", deps=_deps())
    items = _tool_returns(messages, "search_cosmetics")[0]
    # "Spy Shades" hits both keywords, "Spy Fedora" only one.
    assert [i["defindex"] for i in items][:2] == [2, 1]


async def test_search_ignores_keywords_past_the_cap() -> None:
    # Models pad queries with dozens of synonyms; past a point every item matches some
    # keyword and the results are noise, so only the leading keywords count.
    padding = " ".join(f"junk{i}" for i in range(MAX_QUERY_KEYWORDS))
    agent = build_agent(
        _calls_then_finishes(
            "search_cosmetics", {"used_by": "Spy", "query": f"{padding} fedora"}
        )
    )
    with capture_run_messages() as messages:
        await agent.run("padded", deps=_deps())
    assert _tool_returns(messages, "search_cosmetics")[0] == []


async def test_search_returns_empty_when_no_keyword_matches() -> None:
    agent = build_agent(
        _calls_then_finishes("search_cosmetics", {"used_by": "Spy", "query": "rocket"})
    )
    with capture_run_messages() as messages:
        await agent.run("rocket", deps=_deps())
    assert _tool_returns(messages, "search_cosmetics")[0] == []


async def test_search_respects_limit() -> None:
    agent = build_agent(
        _calls_then_finishes("search_cosmetics", {"used_by": "Spy", "limit": 1})
    )
    with capture_run_messages() as messages:
        await agent.run("dress my Spy", deps=_deps())
    assert len(_tool_returns(messages, "search_cosmetics")[0]) == 1


async def test_get_cosmetic_joins_price() -> None:
    agent = build_agent(_calls_then_finishes("get_cosmetic", {"defindex": 1}))
    with capture_run_messages() as messages:
        await agent.run("tell me about the fedora", deps=_deps())
    item = _tool_returns(messages, "get_cosmetic")[0]
    assert item["name"] == "Spy Fedora"
    assert item["equip_regions"] == ["hat"]
    assert item["price"] == {"currency": "metal", "value": 5.0, "value_high": 6.0}


async def test_get_cosmetic_returns_none_for_unknown_defindex() -> None:
    agent = build_agent(_calls_then_finishes("get_cosmetic", {"defindex": 999}))
    with capture_run_messages() as messages:
        await agent.run("what is 999", deps=_deps())
    assert _tool_returns(messages, "get_cosmetic")[0] is None


async def test_unpriced_item_reports_null_price() -> None:
    agent = build_agent(_calls_then_finishes("get_cosmetic", {"defindex": 2}))
    with capture_run_messages() as messages:
        await agent.run("what are the shades worth", deps=_deps())
    assert _tool_returns(messages, "get_cosmetic")[0]["price"] is None


class _StubWiki:
    """Stands in for the wiki client; mirrors the stub used in test_api."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    async def query(self, **params):
        if self.fail:
            raise RuntimeError("wiki unreachable")
        return {
            "parse": {
                "title": "Spy Fedora",
                "wikitext": "'''The Spy Fedora''' is a dapper hat worn by lawmen "
                "and the men they chase.",
            }
        }


def _deps_with_lore(fail: bool = False) -> LoadoutDeps:
    deps = _deps()
    deps.lore = LoreService(_StubWiki(fail=fail))
    return deps


async def test_get_item_lore_returns_the_wiki_summary() -> None:
    agent = build_agent(_calls_then_finishes("get_item_lore", {"defindex": 1}))
    with capture_run_messages() as messages:
        await agent.run("what's the fedora about", deps=_deps_with_lore())
    summary = _tool_returns(messages, "get_item_lore")[0]
    assert "dapper hat worn by lawmen" in summary


async def test_get_item_lore_is_none_without_a_lore_service() -> None:
    # Style reasoning is optional; the tool must degrade rather than explode.
    agent = build_agent(_calls_then_finishes("get_item_lore", {"defindex": 1}))
    with capture_run_messages() as messages:
        await agent.run("what's the fedora about", deps=_deps())
    assert _tool_returns(messages, "get_item_lore")[0] is None


async def test_get_item_lore_is_none_when_the_wiki_fails() -> None:
    agent = build_agent(_calls_then_finishes("get_item_lore", {"defindex": 1}))
    with capture_run_messages() as messages:
        await agent.run("what's the fedora about", deps=_deps_with_lore(fail=True))
    assert _tool_returns(messages, "get_item_lore")[0] is None


async def test_get_item_lore_is_none_for_an_unknown_item() -> None:
    agent = build_agent(_calls_then_finishes("get_item_lore", {"defindex": 999}))
    with capture_run_messages() as messages:
        await agent.run("what's 999 about", deps=_deps_with_lore())
    assert _tool_returns(messages, "get_item_lore")[0] is None


async def test_check_conflicts_flags_overlapping_regions() -> None:
    # 1 and 3 both occupy "hat".
    agent = build_agent(_calls_then_finishes("check_conflicts", {"defindexes": [1, 3]}))
    with capture_run_messages() as messages:
        await agent.run("can I wear both", deps=_deps())
    clashes = _tool_returns(messages, "check_conflicts")[0]
    assert len(clashes) == 1
    assert {clashes[0]["a"], clashes[0]["b"]} == {1, 3}
    assert clashes[0]["regions"] == ["hat"]
    # Names are included so the model can explain the clash without a second lookup.
    assert {clashes[0]["a_name"], clashes[0]["b_name"]} == {"Spy Fedora", "Scout Cap"}


async def test_check_conflicts_allows_distinct_regions() -> None:
    agent = build_agent(_calls_then_finishes("check_conflicts", {"defindexes": [1, 2]}))
    with capture_run_messages() as messages:
        await agent.run("can I wear both", deps=_deps())
    assert _tool_returns(messages, "check_conflicts")[0] == []


async def test_check_conflicts_ignores_unknown_defindexes() -> None:
    agent = build_agent(
        _calls_then_finishes("check_conflicts", {"defindexes": [1, 999]})
    )
    with capture_run_messages() as messages:
        await agent.run("can I wear both", deps=_deps())
    assert _tool_returns(messages, "check_conflicts")[0] == []


def test_agent_caps_output_tokens() -> None:
    # Providers reserve credit against max_tokens, and the default (64k) is both wasteful
    # and enough to get a small-balance account rejected outright. Replies are short.
    agent = build_agent(TestModel())
    assert agent.model_settings["max_tokens"] == MAX_OUTPUT_TOKENS
    assert MAX_OUTPUT_TOKENS <= 4096


def test_misconfigured_provider_disables_chat_instead_of_crashing() -> None:
    # Chat is optional; a bad LLM_MODEL must not take the whole API down at boot.
    settings = LLMSettings(
        model="nonsense-provider:whatever", api_key="x", base_url=None, max_requests=8
    )
    assert build_chat_service(settings, _deps()) is None


def test_valid_provider_builds_a_service() -> None:
    settings = LLMSettings(
        model="anthropic:claude-opus-4-8", api_key="x", base_url=None, max_requests=8
    )
    assert isinstance(build_chat_service(settings, _deps()), LoadoutAgentService)


async def test_service_returns_structured_reply() -> None:
    service = LoadoutAgentService(
        build_agent(_calls_then_finishes("search_cosmetics", {"used_by": "Spy"})),
        _deps(),
    )
    result = await service.reply("dress my Spy", history=None)
    assert result.output.suggested_defindexes == [1, 2]


async def test_stream_reply_reports_each_tool_then_the_result() -> None:
    service = LoadoutAgentService(build_agent(TestModel()), _deps())
    events = [e async for e in service.stream_reply("dress my Spy", history=None)]

    assert events[-1]["kind"] == "final"
    tools = [e["name"] for e in events if e["kind"] == "tool"]
    # TestModel exercises every registered tool, so each should be announced.
    assert set(tools) == {
        "search_cosmetics",
        "get_cosmetic",
        "check_conflicts",
        "get_item_lore",
    }
    # Progress has to arrive before the answer or streaming buys us nothing.
    assert all(e["kind"] == "tool" for e in events[:-1])


async def test_stream_reply_surfaces_errors_as_an_event() -> None:
    # Mid-stream the HTTP status is already sent, so failures travel as data.
    service = LoadoutAgentService(build_agent(TestModel()), _deps(), max_requests=0)
    events = [e async for e in service.stream_reply("dress my Spy", history=None)]
    assert events[-1]["kind"] == "error"


async def test_service_round_trips_history() -> None:
    service = LoadoutAgentService(
        build_agent(_calls_then_finishes("search_cosmetics", {"used_by": "Spy"})),
        _deps(),
    )
    first = await service.reply("dress my Spy", history=None)
    history = first.all_messages()
    second = await service.reply("and a hat?", history=history)
    # The second run must see the first turn, not start cold.
    assert len(second.all_messages()) > len(history)
