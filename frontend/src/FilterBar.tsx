import type { FilterState, Scope, SortKey } from "./filters";
import { DEFAULT_FILTERS, activeFilterCount } from "./filters";

const SCOPES: { value: Scope; label: string; title: string }[] = [
  { value: "any", label: "Any", title: "Every cosmetic this class can wear" },
  { value: "one", label: "One", title: "Wearable by this class only" },
  { value: "multi", label: "Multi", title: "Wearable by 2 to 8 classes" },
  { value: "all", label: "All", title: "Wearable by all nine classes" },
];

// Slider ceiling in refined metal. Not a hard catalog limit -- items above it just
// require typing rather than dragging -- but 100 ref covers the overwhelming majority
// of tradable cosmetics, so it stays the useful end of the range.
const MAX_BUDGET_REF = 100;
const DEFAULT_BUDGET_REF = 20;

const TOGGLES: { key: keyof FilterState; label: string; title: string }[] = [
  {
    key: "noClashes",
    label: "No clashes",
    title: "Dim cosmetics that conflict with your loadout",
  },
  { key: "equippedOnly", label: "Equipped only", title: "Show only what you're wearing" },
  { key: "paintable", label: "Paintable", title: "Only cosmetics that accept paint" },
  { key: "hasStyles", label: "Has styles", title: "Only cosmetics with style variants" },
  {
    key: "hideHalloween",
    label: "Hide Halloween",
    title: "Hide items only wearable during Halloween or a full moon",
  },
];

export default function FilterBar({
  state,
  onChange,
}: {
  state: FilterState;
  onChange: (next: FilterState) => void;
}) {
  const active = activeFilterCount(state);

  return (
    <div className="filterbar">
      <div className="filter-row">
        <span className="filter-label">Scope</span>
        <div className="segmented" role="group" aria-label="Class scope">
          {SCOPES.map((s) => (
            <button
              key={s.value}
              type="button"
              title={s.title}
              aria-pressed={state.scope === s.value}
              onClick={() => onChange({ ...state, scope: s.value })}
            >
              {s.label}
            </button>
          ))}
        </div>

        <span className="filter-label">Sort</span>
        <select
          aria-label="Sort by"
          value={state.sort}
          onChange={(e) => onChange({ ...state, sort: e.target.value as SortKey })}
        >
          <option value="index">Schema order</option>
          <option value="name">Name</option>
          <option value="price">Price</option>
        </select>
        <button
          type="button"
          className="sort-dir"
          aria-pressed={state.desc}
          title={state.desc ? "Descending" : "Ascending"}
          onClick={() => onChange({ ...state, desc: !state.desc })}
        >
          {state.desc ? "↓" : "↑"}
        </button>

        <span className="filter-label">Budget</span>
        <button
          type="button"
          className="filter-chip"
          title="Cap the grid to items priced at or under this many refined metal. Unpriced items and keys convert automatically; a currency with no ref rate is excluded rather than guessed."
          aria-pressed={state.maxRef !== null}
          onClick={() =>
            onChange({
              ...state,
              maxRef: state.maxRef === null ? DEFAULT_BUDGET_REF : null,
            })
          }
        >
          {state.maxRef === null ? "Any price" : `≤ ${state.maxRef} ref`}
        </button>
        {state.maxRef !== null && (
          <input
            type="range"
            className="budget-slider"
            min={1}
            max={MAX_BUDGET_REF}
            step={1}
            value={state.maxRef}
            aria-label="Maximum price in refined metal"
            onChange={(e) => onChange({ ...state, maxRef: Number(e.target.value) })}
          />
        )}
      </div>

      <div className="filter-row">
        {TOGGLES.map((t) => (
          <button
            key={t.key}
            type="button"
            className="filter-chip"
            title={t.title}
            aria-pressed={Boolean(state[t.key])}
            onClick={() => onChange({ ...state, [t.key]: !state[t.key] })}
          >
            {t.label}
          </button>
        ))}
        {active > 0 && (
          <button
            type="button"
            className="filter-clear"
            onClick={() => onChange(DEFAULT_FILTERS)}
          >
            Clear {active}
          </button>
        )}
      </div>
    </div>
  );
}
