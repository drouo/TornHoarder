import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.autoupdate_server import build_server
from tma.foreign_stock import StockLog
from tma.travel_plan import arrival_samples, candidates, load_history, plan_trips, sell_prices, write_prices

STEP = 300  # the logger's 5 minutes


def build(d, mex_qty, can_qty):
    """Log a feed over time: mex_qty(i)/can_qty(i) give each item's stock at step i."""
    log = StockLog(Path(d) / "s.csv", Path(d) / "u.csv")
    n = 200
    for i in range(n):
        t = 1_000_000 + i * STEP
        log.record({
            "mex": {"update": t, "stocks": [{"id": 1, "name": "Flipper", "quantity": mex_qty(i), "cost": 1000},
                                            {"id": 3, "name": "Filler", "quantity": 100, "cost": 1000}]},
            "can": {"update": t, "stocks": [{"id": 2, "name": "Steady", "quantity": can_qty(i), "cost": 1000}]},
        })
    return load_history(Path(d) / "s.csv", Path(d) / "u.csv"), 1_000_000 + (n - 1) * STEP


class TravelPlanTest(unittest.TestCase):
    def test_odds_follow_the_cycle(self):
        # In stock for 30 minutes, then sold out for 30, over and over.
        with TemporaryDirectory() as d:
            h, now = build(d, lambda i: 50 if (i // 6) % 2 == 0 else 0, lambda i: 50)
            # An hour later the cycle is back where it started.
            later = arrival_samples(h, ("mex", 1), 3600, 18, 2)
            self.assertTrue(later and all(q >= 18 for q in later))
            # Half an hour later, it has always sold out.
            half = arrival_samples(h, ("mex", 1), 1800, 18, 2)
            self.assertTrue(half and all(q == 0 for q in half))

    def test_ranks_by_profit_per_hour_with_fallbacks(self):
        with TemporaryDirectory() as d:
            h, now = build(d, lambda i: 0, lambda i: 50)
            sell = {1: 11_000, 2: 3000, 3: 1500}
            df = plan_trips(h, now, sell, cap=18, speed=0.5, draws=200)
            self.assertEqual(list(df["Country"]), ["Canada", "Mexico"])
            can = df.iloc[0]
            self.assertEqual(can["Chance of a full load"], 100)
            self.assertEqual(can["Expected trip profit"], 18 * 2000)
            # Mexico's best item never comes back: the load is all Filler at $500.
            mex = df.iloc[1]
            self.assertEqual(mex["Best item"], "Flipper")
            self.assertEqual(mex["Chance of a full load"], 0)
            self.assertEqual(mex["Expected trip profit"], 18 * 500)

    def test_sell_price_is_under_the_cheapest_listing_capped_at_market_value(self):
        rows = {1: {"price_1": 12_000, "amount_1": 3}, 2: {"price_1": 9000, "amount_1": 1}, 3: {}}
        sell = sell_prices(rows, {1: 10_000, 2: 20_000, 3: 5000})
        self.assertAlmostEqual(sell[1], 10_000 * 0.95)
        self.assertAlmostEqual(sell[2], 8999 * 0.95)
        self.assertAlmostEqual(sell[3], 5000 * 0.95)

    def test_candidates_need_a_margin_at_market_value(self):
        with TemporaryDirectory() as d:
            h, _ = build(d, lambda i: 10, lambda i: 10)
            self.assertEqual(candidates(h, {1: 2000, 2: 1000, 3: 1100}), [1, 3])

    def test_prices_reach_the_userscript_through_the_local_server(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "travel_prices.json"
            server = build_server(Path(d) / "x.user.js", port=0, travel_path=path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_address[1]}/travel-prices"
            try:
                self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()), {"checked": 0, "sell": {}})
                write_prices(path, {261: 28_500.4}, now=100)
                self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()), {"checked": 100, "sell": {"261": 28500}})
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
