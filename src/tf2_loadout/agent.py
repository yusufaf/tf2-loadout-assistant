"""The style-reasoning agent.

Provider-agnostic by construction: the caller hands in a Pydantic AI model spec (see
``config.build_model``) and nothing here knows or cares which vendor is behind it.

The agent recommends loadouts itself -- the tools only retrieve and validate. Pushing
the recommendation into a tool would put the taste logic in Python and reduce the model
to a formatter.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import FunctionToolCallEvent, ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from tf2_loadout.catalog import CatalogService
from tf2_loadout.config import DEFAULT_MAX_REQUESTS, LLMSettings, build_model
from tf2_loadout.lore import LoreService
from tf2_loadout.models import Cosmetic
from tf2_loadout.pricing import PricingService

SYSTEM_PROMPT = """\
You are a Team Fortress 2 cosmetic loadout advisor. You help players assemble
good-looking loadouts for the nine classes: Scout, Soldier, Pyro, Demoman, Heavy,
Engineer, Medic, Sniper, Spy.

Rules you must follow:
- Never invent item names or defindexes. Call search_cosmetics before naming any item,
  and only ever refer to items it returned.
- Always pass a query to search_cosmetics when the player describes a look; the catalog
  has thousands of items and an unfiltered class search wastes everyone's time.
- search_cosmetics already returns each item's price. Use it to answer budget questions
  directly; only call get_cosmetic when you need the class list or slot for one item.
- Two cosmetics cannot be worn together when their equip regions overlap. Some regions
  clash across different names (for example whole_head against hat), so never judge this
  yourself.
- Put every defindex you intend to recommend into suggested_defindexes, and repeat the
  item names in your message so the player knows what they are.

Keep replies short and in the game's irreverent voice. Prices come from backpack.tf for
the Unique / Tradable / Craftable variant, are denominated in refined metal or keys, and
are often missing entirely -- say so rather than guessing.
"""


class LoadoutReply(BaseModel):
    """The agent's structured answer.

    ``suggested_defindexes`` is what makes the reply actionable: the frontend re-resolves
    them against the real catalog and offers to equip them.
    """

    message: str = Field(description="Reply to the player, in the game's voice.")
    suggested_defindexes: list[int] = Field(
        default_factory=list,
        description="Defindexes of every cosmetic recommended in the message.",
    )


@dataclass
class LoadoutDeps:
    """Services the tools query. Injected per run, never reached through globals."""

    catalog: CatalogService
    pricing: PricingService
    lore: LoreService | None = None


def _price_out(pricing: PricingService, defindex: int) -> dict | None:
    price = pricing.get(defindex)
    if price is None:
        return None
    return {
        "currency": price.currency,
        "value": price.value,
        "value_high": price.value_high,
    }


# A loadout reply is a short paragraph plus a handful of defindexes. Providers reserve
# credit against max_tokens, so leaving it at the 64k default is both wasteful and enough
# to get a small-balance account rejected before the request even runs.
MAX_OUTPUT_TOKENS = 2048

# Past a dozen or so keywords every item matches something and the ranking degrades into
# noise, so only the leading keywords count.
MAX_QUERY_KEYWORDS = 12


def _keyword_matches(items: list[Cosmetic], query: str) -> list[Cosmetic]:
    """Rank items by how many of the query's keywords appear in their name.

    Models describe a look with a bag of words ("cop detective officer badge") rather
    than an exact substring, so match on any keyword and sort the best hits first.
    Items matching nothing are dropped.
    """
    keywords = [word for word in query.lower().split() if word][:MAX_QUERY_KEYWORDS]
    if not keywords:
        return items
    scored = []
    for item in items:
        name = item.name.lower()
        score = sum(1 for word in keywords if word in name)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored]


def _summary(cosmetic: Cosmetic, pricing: PricingService) -> dict:
    """Shape for search hits: enough to pick items without a follow-up call each.

    Price belongs here even though it costs tokens -- budget questions ("something under
    3 ref") are common, and without it the model has to call get_cosmetic per candidate,
    which exhausts the per-turn request limit.
    """
    return {
        "defindex": cosmetic.defindex,
        "name": cosmetic.name,
        "equip_regions": sorted(cosmetic.equip_regions),
        "price": _price_out(pricing, cosmetic.defindex),
    }


def build_agent(
    model: str | Model, *, instructions: str = SYSTEM_PROMPT
) -> Agent[LoadoutDeps, LoadoutReply]:
    """Build a fresh agent. Constructed per call so tests can override in isolation."""
    agent = Agent(
        model,
        deps_type=LoadoutDeps,
        output_type=LoadoutReply,
        instructions=instructions,
        model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
    )

    @agent.instructions
    def catalog_size(ctx: RunContext[LoadoutDeps]) -> str:
        return f"The catalog holds {len(ctx.deps.catalog)} cosmetics."

    @agent.tool
    def search_cosmetics(
        ctx: RunContext[LoadoutDeps],
        used_by: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Find cosmetics, optionally narrowed to a class and a name substring.

        Args:
            used_by: Class name to restrict to, e.g. "Spy". Omit to search all classes.
            query: Space-separated keywords; items matching the most are returned first.
            limit: Maximum items to return.
        """
        # catalog.search ignores the class filter, so compose the two by hand -- the
        # same way the /cosmetics route does.
        items = ctx.deps.catalog.for_class(used_by) if used_by else ctx.deps.catalog.all()
        if query:
            items = _keyword_matches(items, query)
        return [_summary(c, ctx.deps.pricing) for c in items[:limit]]

    @agent.tool
    def check_conflicts(
        ctx: RunContext[LoadoutDeps], defindexes: list[int]
    ) -> list[dict]:
        """Report which of the given cosmetics cannot be worn together.

        An empty list means the loadout is valid. Unknown defindexes are ignored.

        Args:
            defindexes: The cosmetics to check against each other.
        """
        cosmetics = [c for di in defindexes if (c := ctx.deps.catalog.get(di))]
        # Go through the catalog so the items_game conflict matrix applies -- it catches
        # clashes between differently-named regions that a set overlap would miss.
        return [
            {
                "a": conflict.a.defindex,
                "a_name": conflict.a.name,
                "b": conflict.b.defindex,
                "b_name": conflict.b.name,
                "regions": sorted(conflict.regions),
            }
            for conflict in ctx.deps.catalog.conflicts(cosmetics)
        ]

    @agent.tool
    def get_cosmetic(ctx: RunContext[LoadoutDeps], defindex: int) -> dict | None:
        """Full detail for one cosmetic, including price. Returns null if unknown.

        Args:
            defindex: The item's defindex, as returned by search_cosmetics.
        """
        cosmetic = ctx.deps.catalog.get(defindex)
        if cosmetic is None:
            return None
        return {
            **_summary(cosmetic, ctx.deps.pricing),
            "used_by_classes": list(cosmetic.used_by_classes),
            "item_slot": cosmetic.item_slot,
        }

    return agent


class LoadoutAgentService:
    """What the API route holds, so the route stays free of Pydantic AI types.

    Mirrors ``LoreService``: constructed with its collaborators, substitutable in tests.
    """

    def __init__(
        self,
        agent: Agent[LoadoutDeps, LoadoutReply],
        deps: LoadoutDeps,
        max_requests: int = DEFAULT_MAX_REQUESTS,
    ):
        self._agent = agent
        self._deps = deps
        self._limits = UsageLimits(request_limit=max_requests)

    async def reply(
        self, prompt: str, history: Sequence[ModelMessage] | None = None
    ) -> AgentRunResult[LoadoutReply]:
        return await self._agent.run(
            prompt,
            deps=self._deps,
            message_history=history,
            usage_limits=self._limits,
        )

    async def stream_reply(
        self, prompt: str, history: Sequence[ModelMessage] | None = None
    ) -> AsyncIterator[dict]:
        """Run a turn, yielding progress as it happens.

        Yields ``{"kind": "tool", "name": ...}`` as each tool is invoked, then exactly
        one terminal ``final`` or ``error`` event. Tool calls are the only progress
        worth reporting: the reply is structured output, so streaming its tokens would
        just leak half-built JSON.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue()
        done = object()

        async def on_events(_ctx, stream) -> None:
            async for event in stream:
                if isinstance(event, FunctionToolCallEvent):
                    queue.put_nowait({"kind": "tool", "name": event.part.tool_name})

        async def run_turn() -> None:
            try:
                result = await self._agent.run(
                    prompt,
                    deps=self._deps,
                    message_history=history,
                    usage_limits=self._limits,
                    event_stream_handler=on_events,
                )
                queue.put_nowait({"kind": "final", "result": result})
            except UsageLimitExceeded:
                queue.put_nowait(
                    {"kind": "error", "detail": "the agent gave up mid-thought"}
                )
            except Exception as exc:
                queue.put_nowait({"kind": "error", "detail": f"model error: {exc}"})
            finally:
                queue.put_nowait(done)  # type: ignore[arg-type]

        task = asyncio.create_task(run_turn())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    return
                yield item
        finally:
            # The client may hang up mid-turn; don't leave the run orphaned.
            if not task.done():
                task.cancel()

    @classmethod
    def from_settings(
        cls, settings: LLMSettings, deps: LoadoutDeps
    ) -> "LoadoutAgentService":
        """Build from env-derived settings. The only place a provider is chosen."""
        return cls(build_agent(build_model(settings)), deps, settings.max_requests)


def build_chat_service(
    settings: LLMSettings, deps: LoadoutDeps
) -> LoadoutAgentService | None:
    """Build the chat service, or None if the provider config is unusable.

    Chat is an optional feature -- a typo in LLM_MODEL should disable it, not stop the
    catalog API from serving.
    """
    if not settings.enabled:
        return None
    try:
        return LoadoutAgentService.from_settings(settings, deps)
    except Exception as exc:
        print(f"chat disabled: {settings.model} could not be configured ({exc})")
        return None
