---
name: Mann Co. Loadout Bench
identity: Mann Co. propaganda poster crossed with the in-game backpack UI
tokens:
  colors:
    paper: "#e8dcc0"        # base ground
    paper-dark: "#d6c4a0"   # secondary surface (tray, active chips)
    paper-edge: "#c2ab7e"   # hairlines, quiet borders
    paper-lit: "#fffdf6"    # lightest surface — inputs, bot chat, saved items
    paper-lit-cool: "#fffefa" # thumbnail base only
    ink: "#2b2620"           # text, all structural borders
    ink-soft: "#5a4f40"      # secondary text, dashed rules
    red: "#b8383b"           # RED team, active class, destructive-hover
    blu: "#5885a2"           # BLU team, region badges, navigation actions
    blu-deep: "#46708a"      # hover/active state of blu, select/slider accent-color
    gold: "#e5c16e"          # save/share hover, chat-equip fill
    gold-deep: "#cf9b45"     # currency, quality edge, selection ring
    orange: "#cf7336"        # interactive link/hover accent (press-kit TF2 orange)
    steel: "#7c7c74"         # muted metadata, footer text
    stamp: "#9e2b2b"         # conflict / destructive only
    blush: "#f7e9e3"         # clash background tint
  typography:
    font-display: "Oswald, system-ui, sans-serif"   # weights 500/600/700
    font-body: "Inter, system-ui, sans-serif"        # weights 400/500/600
    scale:
      xs: 0.7rem
      sm: 0.78rem
      base: 0.85rem
      md: 0.92rem
      lg: 1.1rem
      xl: 1.15rem
      display: "clamp(1.6rem, 4vw, 2.6rem)"
  spacing:
    unit: 4px
    scale: [0.25rem, 0.5rem, 0.75rem, 1rem, 1.25rem, 2rem]
  radii:
    default: 0
    small: 4px      # .segmented, <select>, mobile grid scroll box
    # The budget range-input thumb renders pill-shaped from unstyled native
    # browser defaults — there's no border-radius token to set, and none
    # should be added; leave it native rather than reimplementing the thumb.
  borders:
    hair: "1px solid var(--ink)"
    default: "2px solid var(--ink)"
    heavy: "3px solid var(--ink)"
  shadow:
    default: "0 2px 0 rgb(var(--ink-rgb) / .35), 0 6px 14px rgb(var(--ink-rgb) / .18)"
    hard: "2px 2px 0 rgb(var(--ink-rgb) / .3)"
    bevel: "inset 1px 1px 0 rgb(255 255 255 / .5), inset -1px -1px 0 rgb(var(--ink-rgb) / .18)"
  motion:
    duration: 60ms
    property: transform only
    reduced-motion: transitions disabled entirely
  breakpoint: 900px
---

## Identity

The Mann Co. Loadout Bench is a propaganda poster you can click on — warm paper stock, heavy ink outlines, hard offset "sticker" shadows, uppercase condensed display type, a rubber-stamp warning for conflicts. It is square by default: nothing here has soft rounded corners, soft blurred shadows, or floats and fades. If a new element looks like a modern SaaS card, it's wrong for this app.

## Color roles

| Token | Hex | Use for | Never for |
|---|---|---|---|
| `--paper` | `#e8dcc0` | page background, quiet button fills | body text, borders |
| `--paper-dark` | `#d6c4a0` | secondary surfaces: tray, active-but-not-selected chips | primary CTAs |
| `--paper-edge` | `#c2ab7e` | hairline rules, mobile scroll-box border | structural 2px+ borders (use `--ink`) |
| `--paper-lit` | `#fffdf6` | lightest interactive surface: inputs, bot chat bubbles, saved-loadout rows | page background |
| `--ink` | `#2b2620` | all text, every structural border | decorative fills |
| `--ink-soft` | `#5a4f40` | secondary/meta text, dashed divider rules | primary text, borders |
| `--red` | `#b8383b` | RED team accent, the active class chip | anything that isn't a team/selection marker |
| `--blu` | `#5885a2` | BLU team accent, equip-region badges, the handoff/chat-send actions | destructive or currency UI |
| `--blu-deep` | `#46708a` | hover/focus state of blu elements, `accent-color` on native controls | standalone fills |
| `--gold-deep` | `#cf9b45` | price text, the quality-edge stripe on item cells, selection ring, totals sum | link hovers (use `--orange`) |
| `--gold` | `#e5c16e` | hover fill for save/share buttons and the chat "Equip these" button | body text (fails contrast) |
| `--orange` | `#cf7336` | link/hover accent on non-currency interactive text (bp.tf link, footer links) | quality edge, price, selection |
| `--steel` | `#7c7c74` | muted metadata: unpriced items, bp.tf link idle state, footer copy | primary actions |
| `--stamp` | `#9e2b2b` | conflicts, remove/delete actions, error status — always means "something is wrong" | anything neutral |

**Rule of thumb:** gold-deep is money and quality; blu is navigation and identity; red is team/selection; orange is a plain link hover; stamp is trouble. Don't reach for a color outside this table — extend it deliberately if a new role is genuinely needed.

## Reserved: TF2 item-quality colors

Not wired up. `Cosmetic` (`frontend/src/api.ts`) carries no quality field — `pricing.py` only reads the Unique/Tradable/Craftable price variant, so every item in this catalog *is* Unique. The single gold `border-top` quality edge on `.cell` is therefore already correct and should stay a flat gold-deep, not a per-item color, until quality data actually exists.

If quality ever gets wired, this is the real Valve palette to use (source: [TF2 Wiki](https://wiki.teamfortress.com/wiki/Item_quality)) — do not invent values:

| Quality | Hex |
|---|---|
| Normal | `#B2B2B2` |
| Unique | `#FFD700` |
| Vintage | `#476291` |
| Genuine | `#4D7455` |
| Strange | `#CF6A32` |
| Unusual | `#8650AC` |
| Haunted | `#38F3AB` |
| Collector's | `#AA0000` |
| Decorated Weapon | `#FAFAFA` |
| Community / Self-Made | `#70B04A` |
| Valve | `#A50F79` |

## Press-kit reference palette

The 17-color official TF2 press-kit palette ([lospec](https://lospec.com/palette-list/team-fortress-2-official)), for reference when extending the theme:

`#395c78` `#5b7a8c` `#768a88` `#6b6a65` `#34302d` `#462d26` `#6a4535` `#913a1e` `#bd3b3b` `#9d312f` `#f08149` `#ef9849` `#f5ad87` `#f6b98a` `#f5e7de` `#c1a18a` `#dabdab`

The app's tokens are deliberately not a 1:1 lift: `--paper` (`#e8dcc0`) reads warmer/more saturated than the press-kit ivory `#f5e7de` — that was a choice, not a drift, keep it. `--red` and `--blu` are near-exact matches to the press-kit red/blu family. `--orange` (`#cf7336`) is pulled straight from `#913a1e`'s neighborhood and exists specifically to distinguish "link" from "currency" (gold-deep).

## Typography

Two families, loaded from Google Fonts in `index.html` (no self-hosting, no `@font-face`):

- **`--font-display`: Oswald** (500/600/700) — a condensed grotesque standing in for TF2's proprietary "TF2 Build" headline face. **TF2 Build and TF2 Secondary are Valve-licensed fonts and cannot be shipped in this app.** Do not hotlink or embed them; Oswald is the permanent substitute, not a placeholder.
- **`--font-body`: Inter** (400/500/600) — all body copy, form inputs.

Rule: display type is always uppercase with letter-spacing (0.04–0.14em depending on size); body type is never uppercase and never letter-spaced.

Scale — use these seven sizes, nothing in between:

| Token | rem | Typical use |
|---|---|---|
| `xs` | 0.7 | badges, saved-item meta, chat chips |
| `sm` | 0.78 | filter labels, chat status, footer links |
| `base` | 0.85 | prices, slot names, buttons, chat messages |
| `md` | 0.92 | item cell name |
| `lg` | 1.1 | tray heading |
| `xl` | 1.15 | totals sum |
| `display` | `clamp(1.6rem, 4vw, 2.6rem)` | masthead h1 only |

Constraint to hold, not repeat: Inter only loads 400/500/600, so nothing on `--font-body` should request `font-weight: 700` — the browser fakes it (synthesized bold), which reads slightly blurred/misaligned. `.filter-clear` was dropped to 600 for this reason; `.equipped-tick` was switched to `--font-display` instead, since it wanted a real bold and Oswald loads 700. Follow the same pattern for new Inter-based elements: cap at 600, or switch to display type if 700 is genuinely needed.

## Spacing & layout

4px base unit. Six-step scale: `0.25rem 0.5rem 0.75rem 1rem 1.25rem 2rem` (4/8/12/16/20/32px). Every new padding/margin/gap should land on one of these — don't introduce a seventh value without a real reason.

Layout constants: app caps at `1180px` centered; the bench is a `1fr 340px` grid (content / tray); the item grid uses `repeat(auto-fill, minmax(150px, 1fr))`. One breakpoint, `900px`: below it the bench collapses to one column, the item grid caps at `55vh` with a scrollbar so the tray stays reachable, and the tray un-sticks. Don't add a second breakpoint without a concrete layout problem it solves — the intrinsic `auto-fill`/`flex-wrap`/`clamp()` sizing has carried the app fine so far.

## Components — reuse these, don't rebuild them

| Component | Class | Notes |
|---|---|---|
| Item card | `.cell` | Backpack-slot look: gradient fill, 2px ink border, 4px gold-deep top edge ("quality edge"), hard shadow, `.selected`/`.dimmed` modifiers, `.equipped-tick` badge |
| Tray panel | `.tray` | Sticky, paper-dark, 3px ink border |
| Equipped row | `.slot` | `.clash` modifier switches border/background to stamp/blush |
| Conflict warning | `.stamp` | Rotated -4deg, stamp-colored, uppercase — reserved for the INCOMPATIBLE state, don't reuse the rotation trick elsewhere |
| Class picker | `.class-chip` | `aria-pressed="true"` flips it to `--red` fill + white text/icon |
| Filter toggle | `.filter-chip`, `.sort-dir`, `.segmented button` | Square, hairline border, `aria-pressed="true"` flips to `--red` fill (matches `.class-chip` — see Part 2d below) |
| Destructive filter action | `.filter-clear` | Always `--stamp` filled |
| Chat bubble | `.chat-msg.user` / `.chat-msg.bot` | User: paper-dark, right-indented. Bot: paper-lit, blu left border |
| Suggestion chip | `.chat-chip` | `.clash` modifier same stamp/blush treatment as `.slot.clash` |
| Primary action button | `.save-btn`, `.share-btn`, `.chat-equip`, `.handoff` | 2px ink border + hard shadow; fill communicates role (gold = save-ish, blu = navigate/equip) |

When you need a new interactive element, start from the closest entry above and change only what genuinely differs — a new one-off class is a last resort.

## States, focus, motion, accessibility

- **Focus:** every interactive element gets the same ring — `outline: 3px solid var(--gold-deep); outline-offset: 2px` on `:focus-visible`. No element should ship without one.
- **Toggle state:** driven by `aria-pressed`, styled by attribute selector — never a separate `.active` class fighting `aria-pressed`.
- **Motion:** transitions touch `transform` only, 50–60ms, `ease`. No color, opacity, or layout transitions. `prefers-reduced-motion: reduce` kills all of it (`* { transition: none !important }`) — keep that global rule.
- **Disabled/dimmed:** opacity ladder, not color changes — `0.38` for dimmed-but-clickable cells, `0.55` for disabled form controls, `0.65–0.75` for inactive filter chips. Hover/focus on a dimmed cell should lift it to `0.75`, never to full opacity (it's still filtered out).

## Copy voice

In-universe Mann Co. register in labels and status copy: "Try it on before you trade for it", "Loading the backpack…", "Can't reach the bench", "INCOMPATIBLE". Keep it deadpan-propaganda, not jokey.

**Rule:** flavour belongs in labels, taglines, and ambient status lines. Any error message the user actually needs to act on (a fetch failure with detail, a form validation message) states the real problem plainly — don't dress up something the user must diagnose.

## Don'ts

- No rounded pills or soft corners outside `.segmented`/`<select>` (4px) and the budget range-input's native thumb.
- No soft, blurred `box-shadow` on interactive elements — shadows here are hard-offset, not diffuse.
- No new font families. Oswald + Inter only; no shipping TF2 Build/Secondary.
- No color outside the token table without deliberately extending it here first.
- No raw `rgba(43, 38, 32, …)` / `rgba(207, 155, 69, …)` / `rgba(124, 124, 116, …)` literals — use `rgb(var(--ink-rgb) / <alpha>)` etc.
- No fade or slide animation, no `@keyframes` — this system moves by hard transform only, or not at all.
- No dark mode yet — there's no alternate token set to switch to. Build one deliberately if it's ever needed, don't bolt on a `prefers-color-scheme` query against the light tokens.

## Known gaps

- No dark mode.
- No visual regression test — the frontend test suite (`pnpm test`) covers only pure logic modules (`filters.ts`, `conflicts.ts`); there is no DOM environment. Visual changes must be checked with real screenshots.
- Item-quality colors are reserved but not wired to any data.
- Favicon and social preview metadata are minimal — see `frontend/index.html`.
