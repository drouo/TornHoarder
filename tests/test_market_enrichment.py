import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from tma.market_enrichment import (
    QUALITY_STEP,
    build_stat_payload,
    compute_price_suggestions_for_item,
    enrich_item_orders,
    price_at_quality,
    quality_from_stats,
)


def _weapon_frame(n=30, trend=1.0, seed=0):
    rng = np.random.default_rng(seed)
    qs = np.round(rng.uniform(0, 45, n), 1)
    damage = 50.0 + qs * trend
    accuracy = 55.0 + qs * trend * 0.5
    prices = 1000 + 40 * qs
    return pd.DataFrame(
        {
            "item_id": 7,
            "item_name": "Test Rifle",
            "item_type": "Primary",
            "average_price": float(np.mean(prices)),
            "my_quantity": 1,
            "listing_quality": qs,
            "price": prices,
            "quantity": np.ones(n, dtype=int),
            "listing_damage": damage,
            "listing_accuracy": accuracy,
            "listing_armor": None,
        }
    )


class BuildStatPayloadTest(unittest.TestCase):
    def test_weapon_payload(self):
        payload = build_stat_payload(_weapon_frame())
        self.assertEqual(payload["stat_type"], "Weapon")
        points = payload["points"]
        self.assertGreaterEqual(len(points), 3)
        qs = [p["q"] for p in points]
        self.assertEqual(qs, sorted(qs))
        for p in points:
            self.assertAlmostEqual(p["q"] % QUALITY_STEP, 0, places=3)

    def test_median_quality(self):
        payload = build_stat_payload(_weapon_frame())
        mq = payload["median_quality"]
        self.assertIsNotNone(mq)
        self.assertTrue(0 <= mq <= 50)


class PriceAtQualityTest(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "points": [
                {"q": 10.0, "n": 3, "fast": 100.0, "fair": 110.0, "greedy": 120.0},
                {"q": 20.0, "n": 3, "fast": 200.0, "fair": 210.0, "greedy": 220.0},
            ]
        }

    def test_interpolation(self):
        cur = price_at_quality(self.payload, 15.0)
        self.assertTrue(cur["derived"])
        self.assertEqual(cur["fast"], 150.0)
        self.assertEqual(cur["fair"], 160.0)
        self.assertEqual(cur["greedy"], 170.0)

    def test_clamp_low(self):
        cur = price_at_quality(self.payload, 5.0)
        self.assertEqual(cur["fast"], 100.0)
        self.assertEqual(cur["greedy"], 120.0)

    def test_clamp_high(self):
        cur = price_at_quality(self.payload, 30.0)
        self.assertEqual(cur["fast"], 200.0)
        self.assertEqual(cur["greedy"], 220.0)

    def test_empty_points(self):
        cur = price_at_quality({}, 10.0)
        self.assertFalse(cur["derived"])
        self.assertIsNone(cur["fast"])


class QualityFromStatsTest(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "fit": {
                "type": "Weapon",
                "intercept": 5.0,
                "coef": {"damage": 1.0, "accuracy": 2.0},
                "base": {"damage": 50.0, "accuracy": 40.0},
            }
        }

    def test_weapon_quality(self):
        q = quality_from_stats(self.payload, {"damage": 51.0, "accuracy": 41.0})
        self.assertAlmostEqual(q, 8.0)  # 5 + 1*1 + 2*1

    def test_partial_stats(self):
        q = quality_from_stats(self.payload, {"damage": 52.0})
        self.assertAlmostEqual(q, 7.0)

    def test_no_stats(self):
        self.assertIsNone(quality_from_stats(self.payload, {}))

    def test_armor_fallback(self):
        payload = {"fit": {"type": "Armor", "intercept": None, "coef": {"armor": 1.0}, "base": {"armor": 10.0}}}
        q = quality_from_stats(payload, {"armor": 12.0})
        self.assertAlmostEqual(q, 40.0)  # (12 - 10) * 20


class FastSellTest(unittest.TestCase):
    def _summary(self, prices, qtys):
        df = pd.DataFrame(
            {
                "item_id": 180,
                "item_name": "Bottle of Beer",
                "item_type": "Alcohol",
                "average_price": 900.0,
                "my_quantity": 50,
                "price": [float(p) for p in prices],
                "quantity": [float(q) for q in qtys],
            }
        )
        return compute_price_suggestions_for_item(enrich_item_orders(df))

    def test_fast_sell_is_one_under_the_floor(self):
        s = self._summary([900, 905, 910, 950, 1000] * 20, [50] * 100)
        self.assertEqual(s["floor_price"], 900.0)
        self.assertEqual(s["fast_sell_price"], 899.0)
        self.assertEqual(s["num_clean_listings"], 100)

    def test_fast_sell_never_goes_below_one(self):
        s = self._summary([1, 1, 2], [5, 5, 5])
        self.assertEqual(s["fast_sell_price"], 1.0)


    def test_fat_fingered_listing_does_not_set_the_floor(self):
        s = self._summary([1] + [800_000, 805_000, 810_000] * 33, [1] * 100)
        self.assertEqual(s["floor_price"], 800_000.0)
        self.assertEqual(s["fast_sell_price"], 799_999.0)


if __name__ == "__main__":
    unittest.main()