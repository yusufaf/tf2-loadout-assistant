# TF2 Loadout Assistant

Chat-driven assistant for Team Fortress 2 cosmetic loadouts: style suggestions
("look like a cop"), equip-conflict detection, ratings, and live pricing.

- **Backend:** Python / FastAPI — runs a Claude agent over a TF2 cosmetic catalog
  (Valve item schema), an equip-region conflict engine, backpack.tf pricing, and item
  lore reused from [`tf2-wiki-mcp`](https://github.com/yusufaf/tf2-wiki-mcp).
- **Frontend:** React + Vite — chat UI with per-item cards (image, price, conflict
  badges) and a manual loadout.tf handoff link.

3D model rendering is intentionally **out of scope for v1** — see
`../../.claude/plans/TF2-Loadout-Assistant-v1-Design.md`.

## Development

```sh
uv sync
uv run pytest
```

Copy `.env.example` to `.env` and fill in API keys before running the server.

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
