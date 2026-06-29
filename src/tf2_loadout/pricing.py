"""backpack.tf pricing: parse IGetPrices/v4 into per-defindex prices.

We surface the base variant price (Unique quality, Tradable, Craftable) since that's
what matters for "what does this cosmetic cost". Other qualities (Strange, Unusual) are
ignored for v1.
"""

from __future__ import annotations

import json
from pathlib import Path

from tf2_loadout.models import Price

UNIQUE_QUALITY = "6"
PRICES_CACHE = "prices.json"


def _base_price(item: dict) -> Price | None:
    """Extract the Unique / Tradable / Craftable price from one item entry."""
    entries = (
        item.get("prices", {})
        .get(UNIQUE_QUALITY, {})
        .get("Tradable", {})
        .get("Craftable")
    )
    if not entries:
        return None
    # Craftable is a list (or, for some qualities, a dict keyed by effect); take the
    # first plain entry.
    entry = entries[0] if isinstance(entries, list) else next(iter(entries.values()))
    if not isinstance(entry.get("value"), (int, float)):
        return None
    return Price(
        currency=entry.get("currency", "metal"),
        value=entry["value"],
        value_high=entry.get("value_high"),
        last_update=entry.get("last_update"),
    )


def parse_prices(response: dict) -> dict[int, Price]:
    """Map each item's defindex(es) to its base price."""
    items = response.get("response", {}).get("items", {})
    prices: dict[int, Price] = {}
    for item in items.values():
        price = _base_price(item)
        if price is None:
            continue
        for defindex in item.get("defindex", []):
            prices[defindex] = price
    return prices


class PricingService:
    """Per-defindex price lookups built from an IGetPrices/v4 response."""

    def __init__(self, prices: dict[int, Price]):
        self._prices = prices

    def __len__(self) -> int:
        return len(self._prices)

    @classmethod
    def from_response(cls, response: dict) -> "PricingService":
        return cls(parse_prices(response))

    @classmethod
    def from_cache(cls, cache_dir: str | Path) -> "PricingService":
        raw = json.loads((Path(cache_dir) / PRICES_CACHE).read_text(encoding="utf-8"))
        return cls(
            {
                int(di): Price(**p)
                for di, p in raw.items()
                if isinstance(p.get("value"), (int, float))
            }
        )

    def get(self, defindex: int) -> Price | None:
        return self._prices.get(defindex)

    def save_cache(self, cache_dir: str | Path) -> None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        raw = {
            str(di): {
                "currency": p.currency,
                "value": p.value,
                "value_high": p.value_high,
                "last_update": p.last_update,
            }
            for di, p in self._prices.items()
        }
        (cache_dir / PRICES_CACHE).write_text(json.dumps(raw), encoding="utf-8")
