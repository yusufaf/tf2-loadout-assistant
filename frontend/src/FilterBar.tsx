import type { FilterState, Scope, SortKey } from "./filters";
import { DEFAULT_FILTERS, activeFilterCount } from "./filters";

const SCOPES: { value: Scope; label: string; title: string }[] = [
  { value: "any", label: "Any", title: "Every cosmetic this class can wear" },
  { value: "one", label: "One", title: "Wearable by this class only" },
  { value: "multi", label: "Multi", title: "Wearable by 2 to 8 classes" },
  { value: "all", label: "All", title: "Wearable by all nine classes" },
];

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
