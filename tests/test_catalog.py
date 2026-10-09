"""Behavior of the Valve item-schema parser that feeds the cosmetic catalog.

The Steam ``IEconItems_440/GetSchemaItems`` endpoint returns a flat list of every
item. We keep only cosmetics (items that occupy an equip region) and map each into a
``Cosmetic``.
"""

from tf2_loadout.catalog import ALL_CLASSES, merge_catalog, parse_schema_items
from tf2_loadout.models import ItemAttrs


HAT = {
    "name": "Modest Pile of Hat",
    "defindex": 116,
    "item_class": "tf_wearable",
    "item_type_name": "Hat",
    "item_name": "The Modest Pile of Hat",
    "item_slot": "head",
    "image_url": "http://media.steampowered.com/apps/440/icons/modest_hat.png",
    "used_by_classes": ["Scout", "Soldier", "Pyro"],
    "equip_regions": ["hat"],
}


WEAPON = {
    "name": "Scattergun",
    "defindex": 13,
    "item_class": "tf_weapon_scattergun",
    "item_type_name": "Scattergun",
    "item_name": "Scattergun",
    "item_slot": "primary",
    "used_by_classes": ["Scout"],
    # no equip_regions — weapons do not occupy a cosmetic region
}

MISC_SINGLE_REGION = {
    "name": "Whoopee Cap",
    "defindex": 51,
    "item_class": "tf_wearable",
    "item_name": "The Whoopee Cap",
    "item_slot": "head",
    "used_by_classes": ["Scout", "Engineer"],
    "equip_region": "hat",  # single-string form instead of an array
}


def test_parses_a_cosmetic_into_a_cosmetic_model():
    [cosmetic] = parse_schema_items([HAT])

    assert cosmetic.defindex == 116
    assert cosmetic.name == "The Modest Pile of Hat"  # display name, not internal
    assert cosmetic.equip_regions == frozenset({"hat"})
    assert cosmetic.used_by_classes == ("Scout", "Soldier", "Pyro")
    assert cosmetic.item_slot == "head"
    assert cosmetic.image_url == HAT["image_url"]


def test_drops_items_with_no_equip_region():
    cosmetics = parse_schema_items([HAT, WEAPON])

    assert [c.defindex for c in cosmetics] == [116]


def test_accepts_single_string_equip_region():
    [cosmetic] = parse_schema_items([MISC_SINGLE_REGION])

    assert cosmetic.equip_regions == frozenset({"hat"})


# --- merge_catalog: combine GetSchemaItems metadata with items_game equip regions ---

# Real GetSchemaItems entries have NO equip_regions; regions come from items_game.
SCHEMA_HAT = {
    "defindex": 116,
    "item_class": "tf_wearable",
    "item_name": "The Modest Pile of Hat",
    "item_slot": "head",
    "image_url": "http://media/hat.png",
    "used_by_classes": ["Scout"],
}
SCHEMA_MEDIGUN = {
    "defindex": 29,
    "item_class": "tf_weapon_medigun",
    "item_name": "Medi Gun",
    "used_by_classes": ["Medic"],
}
SCHEMA_WEARABLE_NO_REGION = {
    "defindex": 999,
    "item_class": "tf_wearable",
    "item_name": "Region-less Wearable",
    "used_by_classes": ["Spy"],
}


def test_merge_builds_cosmetic_from_metadata_plus_regions():
    regions = {116: frozenset({"hat"})}

    [cosmetic] = merge_catalog([SCHEMA_HAT], regions)

    assert cosmetic.defindex == 116
    assert cosmetic.name == "The Modest Pile of Hat"
    assert cosmetic.equip_regions == frozenset({"hat"})
    assert cosmetic.used_by_classes == ("Scout",)


def test_merge_excludes_non_wearables_even_with_regions():
    # The medigun has an equip region (medigun_backpack) but is not a cosmetic.
    regions = {29: frozenset({"medigun_backpack"})}

    assert merge_catalog([SCHEMA_MEDIGUN], regions) == []


def test_merge_excludes_wearables_without_resolved_regions():
    assert merge_catalog([SCHEMA_WEARABLE_NO_REGION], {}) == []


def test_merge_attaches_item_attrs_when_present():
    regions = {116: frozenset({"hat"})}
    attrs = {
        116: ItemAttrs(paintable=True, holiday_restriction="halloween_or_fullmoon")
    }

    [cosmetic] = merge_catalog([SCHEMA_HAT], regions, attrs)

    assert cosmetic.paintable is True
    assert cosmetic.holiday_restriction == "halloween_or_fullmoon"


# Styles come from GetSchemaItems, not items_game -- the items_game feed carries no
# style data at all, so this is the only place they can be read.
SCHEMA_STYLED_HAT = {
    "defindex": 52,
    "item_class": "tf_wearable",
    "item_name": "Batter's Helmet",
    "used_by_classes": ["Scout"],
    "styles": [
        {"name": "Hat and Headphones"},
        {"name": "No Hat and No Headphones"},
        {"name": "No Hat"},
    ],
}


def test_merge_reads_style_names_from_the_schema_item():
    regions = {52: frozenset({"hat"})}

    [cosmetic] = merge_catalog([SCHEMA_STYLED_HAT], regions)

    assert cosmetic.styles == (
        "Hat and Headphones",
        "No Hat and No Headphones",
        "No Hat",
    )


def test_merge_tolerates_a_malformed_styles_value():
    regions = {52: frozenset({"hat"})}
    malformed = {**SCHEMA_STYLED_HAT, "styles": "not_a_list"}

    [cosmetic] = merge_catalog([malformed], regions)

    assert cosmetic.styles == ()


def test_merge_defaults_attrs_for_items_with_none():
    # Most of the schema has no paint/holiday/style data at all.
    regions = {116: frozenset({"hat"})}

    [cosmetic] = merge_catalog([SCHEMA_HAT], regions, {})

    assert cosmetic.paintable is False
    assert cosmetic.holiday_restriction is None
    assert cosmetic.styles == ()


# --- all-class items and medals --------------------------------------------------
# Valve omits ``used_by_classes`` for items every class can wear, and ships thousands
# of per-event participation medals as classless wearables.
SCHEMA_ALL_CLASS_HAT = {
    "defindex": 135,
    "item_class": "tf_wearable",
    "item_type_name": "Hat",
    "item_name": "Towering Pillar of Hats",
}
SCHEMA_TOURNAMENT_MEDAL = {
    "defindex": 8000,
    "item_class": "tf_wearable",
    "item_type_name": "Tournament Medal",
    "item_name": "ETF2L Highlander Participant",
}
SCHEMA_COMMUNITY_MEDAL = {
    "defindex": 9000,
    "item_class": "tf_wearable",
    "item_type_name": "Community Medal",
    "item_name": "Community Medal",
}
SCHEMA_DUELING_BADGE = {
    "defindex": 242,
    "item_class": "tf_wearable",
    "item_type_name": "Badge",
    "item_name": "Bronze Dueling Badge",
}
SCHEMA_NOTICE = {
    "defindex": 122,
    "item_class": "tf_wearable",
    "item_type_name": "CheatDetector",
    "item_name": "Your account has been flagged",
    "used_by_classes": [],
}


def test_all_classes_is_the_nine_classes_in_menu_order():
    assert ALL_CLASSES == (
        "Scout", "Soldier", "Pyro", "Demoman", "Heavy",
        "Engineer", "Medic", "Sniper", "Spy",
    )  # fmt: skip


def test_merge_treats_missing_used_by_classes_as_all_nine_classes():
    [cosmetic] = merge_catalog([SCHEMA_ALL_CLASS_HAT], {135: frozenset({"hat"})})

    assert cosmetic.used_by_classes == ALL_CLASSES


def test_merge_treats_null_used_by_classes_as_all_nine_classes():
    raw = {**SCHEMA_ALL_CLASS_HAT, "used_by_classes": None}

    [cosmetic] = merge_catalog([raw], {135: frozenset({"hat"})})

    assert cosmetic.used_by_classes == ALL_CLASSES


def test_merge_keeps_an_explicit_empty_class_list_empty():
    [cosmetic] = merge_catalog([SCHEMA_NOTICE], {122: frozenset({"hat"})})

    assert cosmetic.used_by_classes == ()


def test_merge_excludes_tournament_and_community_medals():
    regions = {8000: frozenset({"medal"}), 9000: frozenset({"medal"})}

    assert merge_catalog([SCHEMA_TOURNAMENT_MEDAL, SCHEMA_COMMUNITY_MEDAL], regions) == []


def test_merge_keeps_medal_region_badges():
    [cosmetic] = merge_catalog([SCHEMA_DUELING_BADGE], {242: frozenset({"medal"})})

    assert cosmetic.used_by_classes == ALL_CLASSES
