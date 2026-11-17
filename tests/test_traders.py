"""Trader price lists (tma.traders): parsing weav3r.dev's JSON, the cache, best offer per item."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma import traders
from tma.config import load_traders, save_traders

# The shape seen live at weav3r.dev/api/pricelist/3878831.
ROWS = [
    {"itemId": -3, "name": "Bunker Bucks", "buyPrice": 0, "bulkThreshold": None, "bulkBuyPrice": None},
    {"itemId": 261, "name": "Wolverine Plushie", "buyPrice": 4273, "bulkThreshold": None, "bulkBuyPrice": None},
    {"itemId": 1, "name": "Hammer", "buyPrice": 0, "bulkThreshold": None, "bulkBuyPrice": None},
    {"name": "broken"},
]
A, B = {"id": 3878831, "name": "Shekels"}, {"id": 42, "name": "Other"}


class TradersTest(unittest.TestCase):
    def test_parse_keeps_only_real_items_they_buy(self):
        self.assertEqual(traders.parse_pricelist(ROWS), {261: 4273.0})
        self.assertEqual(traders.parse_pricelist({"error": "x"}), {})

    def test_refresh_caches_and_keeps_the_last_good_list(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "t.json"
            with patch.object(traders, "fetch_pricelist", lambda s, i: ({261: 4273.0}, None)):
                cache, errors = traders.refresh_prices(path, [A], None, now=1000)
            self.assertEqual((cache["3878831"], errors), ({"checked": 1000, "prices": {"261": 4273.0}}, []))
            calls = []
            with patch.object(traders, "fetch_pricelist", lambda s, i: calls.append(i) or ({}, "HTTPError: 503")):
                cache, _ = traders.refresh_prices(path, [A], None, now=1000 + 60)
                self.assertEqual(calls, [])  # still fresh: not refetched
                cache, errors = traders.refresh_prices(path, [A], None, now=1000 + traders.MAX_AGE_S + 1)
            self.assertEqual(cache["3878831"]["prices"], {"261": 4273.0})
            self.assertEqual(errors, ["Shekels: HTTPError: 503"])

    def test_best_offer_per_item_across_traders(self):
        cache = {"3878831": {"prices": {"261": 4273.0, "1": 10.0}}, "42": {"prices": {"261": 4300.0}}}
        best = traders.best_offers([A, B], cache)
        self.assertEqual(best[261], {"price": 4300.0, "trader_id": 42, "trader": "Other"})
        self.assertEqual(best[1]["trader"], "Shekels")

    def test_traders_default_to_shekels_and_save(self):
        self.assertEqual(load_traders(), [A])
        save_traders([A, B])
        self.assertEqual(load_traders(), [A, B])
        save_traders([])
        self.assertEqual(load_traders(), [])


if __name__ == "__main__":
    unittest.main()
