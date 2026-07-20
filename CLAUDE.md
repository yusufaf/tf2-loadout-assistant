# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A bench for TF2 cosmetic loadouts: browse a class's cosmetics, try them on, get equip-conflict detection and live pricing, and ask an LLM advisor for suggestions from a described style ("look like a cop").

- **Backend** — Python / FastAPI (`src/tf2_loadout/`, ~1400 lines). Catalog from Valve's item schema, conflict engine, backpack.tf pricing, item lore reused from the sibling `tf2-wiki-mcp` project.
- **Frontend** — React + Vite (`frontend/`). Class browser, loadout tray, advisor chat panel.

3D model rendering is intentionally out of scope for v1.

## Commands

```bash
uv sync
cp .env.example .env                      # then fill in the keys you have
uv run tf2-loadout-api                    # http://127.0.0.1:8000
uv run pytest                             # unit tests; never reaches a model or external API
uv run pytest --live                      # also runs tests that hit real APIs

cd frontend && pnpm install && pnpm dev   # http://localhost:5173
```

`tf2-wiki-mcp` is an editable path dependency (`[tool.uv.sources]` → `../tf2-wiki-mcp`). That sibling repo must exist next to this one or `uv sync` fails. Changes to it are picked up live.

## Architecture

**The agent recommends; tools only retrieve and validate.** `agent.py` deliberately keeps taste logic in the model. Moving recommendation into a Python tool would reduce the model to a formatter — that tradeoff was made on purpose, so resist "just compute the best loadout" refactors.

**Invented items are dropped, not displayed.** The single biggest quality risk is a weak model recommending cosmetics it never looked up. Defence is structural: `LoadoutReply.suggested_defindexes` carries every recommended defindex, and the API re-resolves each one against the real catalog before returning. The system prompt also forbids naming an item without calling `search_cosmetics` first. Both layers matter — keep them.

**Provider-agnostic by construction.** `config.py` resolves `LLM_MODEL` (a Pydantic AI `provider:model` string) into a model object; nothing else in the codebase knows the vendor. Each provider reads its own native key env var, and `LLM_API_KEY` is a generic override exported under that native name at startup by `apply_provider_env`. Ollama is keyless and needs an explicit base URL or Pydantic AI refuses to construct — hence `DEFAULT_OLLAMA_BASE_URL`. With no key configured the API still serves the catalog and `/healthz` reports `"chat": false`.

**Equip regions come from two sources that must be merged.** Steam's `GetSchemaItems` gives item metadata but *not* reliable equip regions; `items_game.txt` (VDF) has the regions, often inherited through a `prefab` chain, plus the `equip_conflicts` cross-region matrix. `items_game.py` resolves prefabs recursively (depth-capped at 8) and `catalog.merge_catalog` joins the two. An item is a cosmetic only if it's a `tf_wearable` *and* has resolved regions — that test is what excludes weapons carrying a stray region.

**Conflicts are not just same-region overlap.** `conflicts.find_conflicts` tests every pair; two regions conflict if identical *or* listed against each other in the matrix (`whole_head` vs `hat`). Never judge a conflict by region name equality alone.

**The conflict rule exists twice, on purpose.** `conflicts.py` is the source of truth; `frontend/src/conflicts.ts` is a deliberate port so filter toggles are instant instead of a round-trip per click. `conflicts.test.ts` mirrors `tests/test_conflicts.py` — change the rule in one place and the other's tests should fail. Filtering, sorting, and clash dimming all live in `frontend/src/filters.ts` as pure functions; `App.tsx` owns only the state.

**The catalog cache is versioned.** `equip.json` carries a `version`, and `CatalogService.from_cache` raises `StaleCacheError` on a mismatch rather than serving a catalog where every item silently reads as unpaintable and style-less. There is no refresh script: rebuild with `uv run pytest --live`. Bump `CACHE_VERSION` whenever the file's shape changes.

**Item attributes are resolved separately from equip regions.** `resolve_item_attrs` uses `_flatten_with_prefabs` (own keys shadow inherited ones); `resolve_equip_regions` keeps its own recursion, which stops inheriting the moment a node declares any region. Don't merge the two — a generic key merge would union an item's own `equip_region` with a prefab's `equip_regions`, which is a different answer.

**Paint and styles come from different feeds.** `capabilities.paintable` and `holiday_restriction` are in `items_game`; **style variants are not in `items_game` at all** — not on the item, not on its prefabs. They live in `GetSchemaItems` as a list of `{"name": ...}`, which is why `catalog._styles` reads them off the raw schema item rather than through `ItemAttrs`. This was found by counting real data (0 styled items) after a version that looked correct against fixtures, so verify attribute work against the live cache, not just tests.

**Pricing is best-effort and frequently absent.** `pricing.py` reads only the Unique / Tradable / Craftable variant from backpack.tf `IGetPrices/v4`. The `Craftable` node is a list for most qualities but a dict keyed by effect for others — both forms are handled. Missing prices are normal; surface them as unknown rather than guessing.

**Chat is stateless.** The client sends the whole transcript back each turn. `DEFAULT_MAX_REQUESTS = 25` is a runaway-loop guard sized against measurement (a plain turn is ~8 requests, a hard lore-checking turn hit 13), not a budget cap.

## Testing

Agent tests drive Pydantic AI's `TestModel` / `FunctionModel`, and a session fixture pins `ALLOW_MODEL_REQUESTS = False` so no test can reach a provider by accident. Do not remove that fixture. `*_live.py` test modules and `@pytest.mark.live` cases are skipped unless `--live` is passed.

The frontend has its own suite: `cd frontend && pnpm test` (Vitest). It covers the pure modules only — `conflicts.ts` and `filters.ts`. There is no DOM environment and no component test; keep logic out of components so it stays that way.

## Conventions

- `from __future__ import annotations` everywhere; frozen dataclasses for value types (`Conflict`), Pydantic models for anything crossing the API boundary.
- Module docstrings explain the *why*. Comments mark where an external schema is weird (the VDF duplicate-key form, the backpack.tf list/dict split) — those are load-bearing, not noise.
- `SYSTEM_PROMPT` in `agent.py` is product surface. Its rules encode real failure modes observed with weaker models; change it deliberately and re-run the agent tests.
- Never commit `.env` (it's gitignored). `.env.example` documents the keys.
