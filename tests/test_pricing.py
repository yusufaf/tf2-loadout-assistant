"""Behavior of the backpack.tf IGetPrices/v4 parser.

The response nests prices by item name -> quality -> tradability -> craftability -> a
list of price entries. For v1 we surface the base price (Unique / Tradable / Craftable)
keyed by defindex so it can be joined to a Cosmetic.
"""

from tf2_loadout.models import Price
from tf2_loadout.pricing import parse_prices

RESPONSE = {
    "response": {
        "success": 1,
        "items": {
            "Team Captain": {
                "defindex": [378],
                "prices": {
                    "6": {  # Unique
                        "Tradable": {
                            "Craftable": [
                                {
                                    "currency": "metal",
                                    "value": 1.55,
                                    "value_high": 1.77,
                                    "last_update": 1610000000,
                                }
                            ]
                        }
                    }
                },
            },
            "A Key-Priced Hat": {
                "defindex": [30000],
                "prices": {
                    "6": {"Tradable": {"Craftable": [{"currency": "keys", "value": 2}]}}
                },
            },
            "Untradable Promo": {
                "defindex": [999],
                "prices": {
                    "6": {"Non-Tradable": {"Craftable": [{"currency": "metal", "value": 0.05}]}}
                },
            },
        },
    }
}


def test_parses_base_price_keyed_by_defindex():
    prices = parse_prices(RESPONSE)

    tc = prices[378]
    assert isinstance(tc, Price)
    assert tc.currency == "metal"
    assert tc.value == 1.55
    assert tc.value_high == 1.77


def test_preserves_key_denominated_prices():
    assert parse_prices(RESPONSE)[30000].currency == "keys"


def test_skips_items_without_a_tradable_craftable_price():
    assert 999 not in parse_prices(RESPONSE)


def test_skips_price_entries_with_null_value():
    # Some backpack.tf entries carry a placeholder with null value/currency.
    response = {
        "response": {
            "items": {
                "Bloodhound": {
                    "defindex": [1029],
                    "prices": {
                        "6": {"Tradable": {"Craftable": [{"currency": None, "value": None}]}}
                    },
                }
            }
        }
    }

    assert 1029 not in parse_prices(response)
