import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.quality import (
    armor_quality,
    base_stats_map,
    item_quality,
    premium_multiplier,
    premium_price,
    weapon_quality,
)


class WeaponQualityTest(unittest.TestCase):
    def test_positive_deviation(self):
        self.assertAlmostEqual(weapon_quality(damage=55.0, base_damage=50.0, accuracy=48.0, base_accuracy=45.0), 80.0)

    def test_at_base_is_zero(self):
        self.assertEqual(weapon_quality(50.0, 50.0, 45.0, 45.0), 0.0)


class ArmorQualityTest(unittest.TestCase):
    def test_positive_deviation(self):
        self.assertAlmostEqual(armor_quality(armor=25.0, base_armor=20.0), 100.0)


class ItemQualityTest(unittest.TestCase):
    def test_weapon_dispatch(self):
        q = item_quality("Weapon", {"damage": 55.0, "accuracy": 48.0}, {"damage": 50.0, "accuracy": 45.0})
        self.assertAlmostEqual(q, 80.0)

    def test_armor_dispatch(self):
        q = item_quality("Armor", {"armor": 25.0}, {"armor": 20.0})
        self.assertAlmostEqual(q, 100.0)

    def test_unknown_type_returns_none(self):
        self.assertIsNone(item_quality("Collectible", {}, {}))


class PremiumMultiplierTest(unittest.TestCase):
    def test_below_threshold_is_base(self):
        self.assertEqual(premium_multiplier(10.0), 1.0)

    def test_at_threshold(self):
        self.assertAlmostEqual(premium_multiplier(50.0), 1.0 + 0.5 * 2.0)

    def test_capped_at_max(self):
        self.assertEqual(premium_multiplier(1000.0), 3.0)


class PremiumPriceTest(unittest.TestCase):
    def test_rounds_result(self):
        self.assertEqual(premium_price(1000.0, 10.0), 1000)
        self.assertEqual(premium_price(1000.0, 50.0), round(1000.0 * premium_multiplier(50.0)))


class BaseStatsMapTest(unittest.TestCase):
    def test_extracts_weapon_and_armor(self):
        profiles = {
            "26": {"type": "Weapon", "stats": {"damage": 50.0, "accuracy": 45.0}},
            "641": {"type": "Armor", "stats": {"armor": 20.0}},
            "999": {"type": "Collectible", "stats": None},
        }
        out = base_stats_map(profiles)
        self.assertEqual(out[26], {"type": "Weapon", "damage": 50.0, "accuracy": 45.0})
        self.assertEqual(out[641], {"type": "Armor", "armor": 20.0})
        self.assertNotIn(999, out)


if __name__ == "__main__":
    unittest.main()
