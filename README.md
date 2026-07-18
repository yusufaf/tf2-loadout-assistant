# TF2 Loadout Assistant

A bench for Team Fortress 2 cosmetic loadouts: browse a class's cosmetics, try them on,
and get equip-conflict detection and live pricing. An optional LLM advisor suggests
loadouts from a described style ("look like a cop").

- **Backend:** Python / FastAPI — a TF2 cosmetic catalog (Valve item schema), an
  equip-region conflict engine, backpack.tf pricing, and item lore reused from
  [`tf2-wiki-mcp`](https://github.com/yusufaf/tf2-wiki-mcp).
- **Frontend:** React + Vite — a class browser and loadout tray with per-item cards
  (image, price, conflict badges), local saved loadouts with share links, an advisor
  chat panel, and a manual loadout.tf handoff link.

3D model rendering is intentionally **out of scope for v1** — see
`../../.claude/plans/TF2-Loadout-Assistant-v1-Design.md`.

## The advisor is provider-agnostic

The chat advisor runs on [Pydantic AI](https://ai.pydantic.dev/), so the model is chosen
entirely by one environment variable and switching providers needs no code change:

```sh
LLM_MODEL=anthropic:claude-opus-4-8                 # the default
LLM_MODEL=openrouter:google/gemini-3.1-flash-lite   # anything OpenRouter fronts
LLM_MODEL=ollama:qwen3.5                            # local, no key needed
```

Set `LLM_API_KEY` to the matching key, or export the provider's own variable
(`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, …) if you already have one. With no key
configured the API still serves the catalog; `/healthz` reports `"chat": false` and the
frontend hides the panel.

Weaker models are the main quality risk here — small local models tend to recommend
items they never looked up. The API re-resolves every suggested defindex against the
catalog before returning it, so invented items are dropped rather than displayed.

## Running it

```sh
uv sync
cp .env.example .env     # then fill in the keys you have
uv run tf2-loadout-api   # http://127.0.0.1:8000

cd frontend && pnpm install && pnpm dev   # http://localhost:5173
```

The API serves `/cosmetics`, `/cosmetics/{defindex}`, `/lore/{defindex}`,
`/loadout/conflicts`, `/chat`, and `/healthz`. Chat is stateless — the client sends the
transcript back with each turn.

## Tests

```sh
uv run pytest            # unit tests; never reaches a model or an external API
uv run pytest --live     # also runs the tests that hit real APIs
```

Agent tests drive Pydantic AI's `TestModel` / `FunctionModel`, and a session fixture
pins `ALLOW_MODEL_REQUESTS` to `False`, so no test can reach a provider by accident.

## Credits & licensing

- **Class icons** (`frontend/public/classes/`) are from the
  [Official TF2 Wiki](https://wiki.teamfortress.com/), licensed
  [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/). Note the
  **non-commercial** clause — keep this project non-commercial or replace these assets.
- **Pricing data** is from [backpack.tf](https://backpack.tf/).
- **Item images and the cosmetic schema** are property of Valve Corporation. Team
  Fortress 2 is a trademark of Valve. This is an unofficial fan project, not affiliated
  with or endorsed by Valve.

Attribution is also surfaced in the site footer to satisfy the CC BY-NC-SA terms.
