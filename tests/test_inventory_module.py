import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.http_client import cooldown_seconds
from tma.inventory import (
    InventoryEntry,
    _dedupe,
    _has_more,
    _parse_page,
    inventory_category_for_profile,
    to_rows,
)


class ParsePageTest(unittest.TestCase):
    def test_v2_flat_envelope(self):
        data = {
            "inventory": {
                "items": [
                    {"id": 26, "name": "AK-47", "amount": 1, "equipped": False, "uid": 123, "faction_owned": False},
                    {"id": 26, "name": "AK-47", "amount": 1, "equipped": True, "uid": 456, "faction_owned": False},
                    {"id": 641, "name": "Kevlar Gloves", "amount": 2, "equipped": False, "uid": 789, "faction_owned": True},
                ],
                "timestamp": 1700000000,
            },
            "_metadata": {"total": 3, "links": {}},
        }
        entries = _parse_page(data, "Primary")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].item_id, 26)
        self.assertEqual(entries[0].uid, "123")
        self.assertFalse(entries[0].equipped)
        self.assertTrue(entries[1].equipped)

    def test_faction_owned_skipped(self):
        data = {"inventory": [{"id": 1, "name": "X", "amount": 5, "equipped": False, "uid": 1, "faction_owned": True}]}
        self.assertEqual(_parse_page(data, None), [])

    def test_uid_none(self):
        data = {"inventory": [{"id": 1, "name": "X", "amount": 1, "equipped": False}]}
        entries = _parse_page(data, None)
        self.assertIsNone(entries[0].uid)


class HasMoreTest(unittest.TestCase):
    def test_total(self):
        self.assertFalse(_has_more({"_metadata": {"total": 150}, "inventory": []}, 100, 100))
        self.assertTrue(_has_more({"_metadata": {"total": 250}, "inventory": []}, 100, 100))

    def test_links_next(self):
        self.assertTrue(_has_more({"_metadata": {"links": {"next": "?offset=100"}}}, 100, 100))


class ToRowsTest(unittest.TestCase):
    def test_drops_equipped(self):
        entries = [
            InventoryEntry(26, "AK-47", 1, False, "1"),
            InventoryEntry(26, "AK-47", 1, True, "2"),
        ]
        kept, frame, agg = to_rows(entries)
        self.assertEqual(len(kept), 1)
        self.assertEqual(list(frame["uid"]), ["1"])
        self.assertEqual(agg, [(26, 1)])


class DedupeTest(unittest.TestCase):
    def test_key_is_item_uid(self):
        entries = [
            InventoryEntry(26, "AK-47", 1, False, "1"),
            InventoryEntry(26, "AK-47", 1, False, "1"),
            InventoryEntry(26, "AK-47", 1, False, "2"),
        ]
        self.assertEqual(len(_dedupe(entries)), 2)


class CooldownTest(unittest.TestCase):
    def test_parses_reset(self):
        self.assertEqual(cooldown_seconds("Too many calls. Resets at 0:00:20", default=99), 21.0)

    def test_capped_at_60(self):
        self.assertEqual(cooldown_seconds("Too many calls. Resets at 5:00:00", default=99), 60.0)

    def test_default(self):
        self.assertEqual(cooldown_seconds(None, default=7), 7.0)


class InventoryCategoryForProfileTest(unittest.TestCase):
    def test_weapon_uses_its_category(self):
        self.assertEqual(inventory_category_for_profile({"type": "Weapon", "category": "Primary"}), "Primary")

    def test_weapon_unknown_category_is_none(self):
        self.assertIsNone(inventory_category_for_profile({"type": "Weapon", "category": "Exotic"}))

    def test_armor_maps_to_defensive(self):
        # Profiles never carry a usable category for armor (it's always None),
        # so this mapping can't be read off the profile itself.
        self.assertEqual(inventory_category_for_profile({"type": "Armor", "category": None}), "Defensive")

    def test_other_type_matches_itself(self):
        self.assertEqual(inventory_category_for_profile({"type": "Drug"}), "Drug")

    def test_type_not_an_inventory_category_is_none(self):
        self.assertIsNone(inventory_category_for_profile({"type": "Unused"}))

    def test_missing_profile_is_none(self):
        self.assertIsNone(inventory_category_for_profile(None))


if __name__ == "__main__":
    unittest.main()