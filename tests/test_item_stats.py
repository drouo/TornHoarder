import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.item_stats import StatValueModel, item_features, item_model_key, sidecar, stat_warnings
import pandas as pd


class ItemModelKeyTest(unittest.TestCase):
    def test_weapon_in_known_category(self):
        self.assertEqual(item_model_key({"type": "Weapon", "category": "Primary"}), "Primary")

    def test_weapon_in_unknown_category_is_none(self):
        self.assertIsNone(item_model_key({"type": "Weapon", "category": "Exotic"}))

    def test_armor(self):
        self.assertEqual(item_model_key({"type": "Armor"}), "Armor")

    def test_other_type(self):
        self.assertIsNone(item_model_key({"type": "Collectible"}))


class ItemFeaturesTest(unittest.TestCase):
    def test_weapon_features(self):
        feats = item_features({"type": "Weapon", "stats": {"damage": 50.0, "accuracy": 45.0}})
        self.assertEqual(feats, {"damage": 50.0, "accuracy": 45.0, "armor": 0.0})

    def test_weapon_without_stats_is_none(self):
        self.assertIsNone(item_features({"type": "Weapon", "stats": {}}))

    def test_armor_features(self):
        feats = item_features(
            {"type": "Armor", "coverage": [{"name": "Chest", "value": 20.0}, {"name": "Full Body", "value": 5.0}]}
        )
        self.assertEqual(feats["full_body"], 5.0)
        self.assertEqual(feats["coverage_avg"], 20.0)
        self.assertEqual(feats["coverage_parts"], 1.0)

    def test_armor_without_coverage_is_none(self):
        self.assertIsNone(item_features({"type": "Armor", "coverage": []}))


def _synthetic_weapon_profiles(n: int = 25) -> dict:
    """Prices exactly follow a known log-linear model so the fit should be near-perfect."""
    intercept, c_damage, c_accuracy, c_circ = 7.0, 0.05, 0.03, -0.05
    profiles = {}
    for i in range(n):
        damage = 50.0 + i
        accuracy = 45.0 + (i % 5)
        circulation = 1000 + i * 100
        logp = intercept + c_damage * damage + c_accuracy * accuracy + c_circ * math.log(circulation)
        profiles[str(i)] = {
            "id": i,
            "name": f"Weapon {i}",
            "type": "Weapon",
            "category": "Primary",
            "circulation": circulation,
            "market_price": math.exp(logp),
            "stats": {"damage": damage, "accuracy": accuracy},
        }
    return profiles


class StatValueModelTest(unittest.TestCase):
    def test_fits_and_ranks_by_predicted_value(self):
        profiles = _synthetic_weapon_profiles()
        model = StatValueModel()
        model.fit(profiles)

        self.assertIn("Primary", model.models)
        self.assertGreater(model.models["Primary"]["r2"], 0.9)

        low = sidecar(model, profiles, 0)  # lowest damage/accuracy in the set
        high = sidecar(model, profiles, len(profiles) - 1)
        self.assertTrue(low.has_stats)
        self.assertLess(low.implied_value, high.implied_value)

    def test_sidecar_unknown_item(self):
        info = sidecar(StatValueModel(), {}, 999)
        self.assertFalse(info.has_stats)
        self.assertIsNone(info.implied_value)

    def test_too_few_samples_skips_fit(self):
        profiles = _synthetic_weapon_profiles(n=5)
        model = StatValueModel()
        model.fit(profiles)
        self.assertNotIn("Primary", model.models)


class StatWarningsTest(unittest.TestCase):
    def test_flags_underpriced_high_value_item(self):
        profiles = _synthetic_weapon_profiles()
        model = StatValueModel()
        model.fit(profiles)

        df_summary = pd.DataFrame(
            [{"item_id": len(profiles) - 1, "item_name": "Weapon high", "my_quantity": 1, "fair_price": 1.0}]
        )
        warnings = stat_warnings(model, profiles, df_summary)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0]["elevated"] or warnings[0]["ratio"] > 1.0)


if __name__ == "__main__":
    unittest.main()
