# TF2 Loadout Assistant

A bench for Team Fortress 2 cosmetic loadouts: browse a class's cosmetics, try them on,
and get equip-conflict detection and live pricing. An optional LLM advisor suggests
loadouts from a described style ("look like a cop").

- **Backend:** Python / FastAPI — a TF2 cosmetic catalog (Valve item schema), an
  equip-region conflict engine, backpack.tf pricing, and item lore reused from
  [`tf2-wiki-mcp`](https://github.com/yusufaf/tf2-wiki-mcp).
- **Frontend:** React + Vite — a class browser and loadout tray with per-item cards
  (image, price, conflict badges), filters for class scope, sort order (including
  price), a refined-metal budget cap, paint, styles and Halloween restrictions, local
  saved loadouts with share links, a budget-aware advisor chat panel, and a manual
  loadout.tf handoff link.

3D model rendering is intentionally **out of scope for v1**.

Frontend visual/styling conventions live in [`DESIGN.md`](./DESIGN.md).

## The advisor runs on your own API key

The chat advisor runs on [Pydantic AI](https://ai.pydantic.dev/), and the server holds
no LLM credential at all. The first time you open the advisor it asks for a provider
(Anthropic, OpenAI, or OpenRouter — one fixed model each) and an API key. The key is
kept in your browser's `localStorage` only; it travels with each message as a request
header, the server builds a model for that one turn, and nothing is stored or logged
server-side. Clear it any time with "Forget key".

`/healthz` reports `"chat": true` whenever the service built; a turn without a key, or
with one the provider rejects, is a `401` and the panel reopens the key form.

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

The API serves `/cosmetics`, `/cosmetics/{defindex}`, `/equip-conflicts`,
`/lore/{defindex}`, `/loadout/conflicts`, `/chat`, and `/healthz`. Chat is stateless —
the client sends the transcript back with each turn.

The catalog is served from `.cache/`, which is built by the live schema test. If the API
exits with a `StaleCacheError`, the cache predates the current format: rebuild it with
`STEAM_API_KEY` set and `uv run pytest --live`.

## Tests

```sh
uv run pytest            # unit tests; never reaches a model or an external API
uv run pytest --live     # also runs the tests that hit real APIs, and rebuilds .cache

cd frontend && pnpm test # Vitest, covering the pure filter and conflict modules
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
