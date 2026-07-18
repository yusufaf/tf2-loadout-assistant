"""One real round trip against whatever LLM_MODEL points at.

Skipped unless --live. Asserts the shape of a real turn rather than its wording: the
model must recommend items that actually exist and that can be worn together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps
from tf2_loadout.catalog import CatalogService
from tf2_loadout.config import LLMSettings, load_env
from tf2_loadout.pricing import PricingService

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"

pytestmark = pytest.mark.live


@pytest.fixture
def deps() -> LoadoutDeps:
    if not CACHE_DIR.exists():
        pytest.skip("no catalog cache; run the schema refresh first")
    return LoadoutDeps(
        catalog=CatalogService.from_cache(CACHE_DIR),
        pricing=PricingService.from_cache(CACHE_DIR),
        lore=None,  # keep the turn off the rate-limited wiki
    )


@pytest.fixture
def service(deps: LoadoutDeps) -> LoadoutAgentService:
    load_env()
    settings = LLMSettings.from_env()
    if not settings.enabled:
        pytest.skip("no LLM configured (see .env.example)")
    return LoadoutAgentService.from_settings(settings, deps)


async def test_real_model_suggests_real_wearable_items(
    service: LoadoutAgentService, deps: LoadoutDeps
) -> None:
    result = await service.reply("Give me a cop-style Spy loadout.", history=None)
    catalog = deps.catalog

    assert result.output.message.strip()
    suggested = result.output.suggested_defindexes
    assert suggested, "the model recommended nothing"

    items = [catalog.get(di) for di in suggested]
    assert all(item is not None for item in items), (
        f"invented defindexes: {[d for d, i in zip(suggested, items) if i is None]}"
    )
    # Every suggestion should be wearable by the class that was asked for.
    assert all("Spy" in item.used_by_classes for item in items)
    # And the set has to be actually equippable together.
    assert catalog.conflicts(items) == []


async def test_real_model_carries_context_across_turns(
    service: LoadoutAgentService,
) -> None:
    first = await service.reply("Give me a cop-style Spy loadout.", history=None)
    second = await service.reply(
        "Swap the hat for something cheaper.", history=first.all_messages()
    )
    assert second.output.suggested_defindexes
    assert len(second.all_messages()) > len(first.all_messages())
