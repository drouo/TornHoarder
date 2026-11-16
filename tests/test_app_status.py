import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.app_status import FRESH, NEVER, STALE, ago, jobs, last_csv_time, write_last_run

MIDNIGHT = 20_406 * 86_400
NOW = MIDNIGHT + 12 * 3600


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def board(self, has_minimums=True, version="1.6.0"):
        d = self.d
        got = jobs(NOW, last_run=d / "last_run.json", script=d / "s.user.js", app_version=version,
                   stock_status=d / "stock.json", has_minimums=has_minimums, shop_plan=d / "plan.json",
                   travel_prices=d / "travel.json", foreign_updates=d / "updates.csv")
        return {j.name: j for j in got}

    def put(self, name, data):
        (self.d / name).write_text(json.dumps(data), encoding="utf-8")

    def test_nothing_run_yet_says_what_to_do_first(self):
        b = self.board(has_minimums=False)
        self.assertTrue(all(j.state == NEVER for j in b.values()))
        self.assertEqual(b["Sell prices"].todo, "Click Run to price your items.")
        self.assertEqual(b["Keep stocked"].detail, "No minimums set.")

    def test_fresh_results(self):
        write_last_run(self.d / "last_run.json", 143, "API inventory", now=NOW - 7200)
        (self.d / "s.user.js").write_text("// @version      1.6.0.1794700000\n", encoding="utf-8")  # build-time suffix
        self.put("stock.json", {"checked": NOW - 600, "short": []})
        self.put("plan.json", {"checked": MIDNIGHT + 60, "route": [{}, {}]})
        self.put("travel.json", {"checked": NOW - 300, "sell": {}})
        (self.d / "updates.csv").write_text(f"seen,country\n{NOW - 900},mex\n{NOW - 240},can\n", encoding="utf-8")
        b = self.board()
        self.assertTrue(all(j.state == FRESH for j in b.values()), {k: j.state for k, j in b.items()})
        self.assertEqual(b["Sell prices"].detail, "143 items priced 2h ago.")
        self.assertEqual(b["City shop buys"].detail, "2 shop(s) planned, scanned 12h ago.")
        self.assertEqual(b["Foreign stock logger"].detail, "Last update 4 min ago.")

    def test_stale_by_each_tools_own_clock(self):
        write_last_run(self.d / "last_run.json", 10, "API inventory", now=NOW - 7 * 3600)
        (self.d / "s.user.js").write_text("// @version      1.5.1\n", encoding="utf-8")
        self.put("stock.json", {"checked": NOW - 600, "short": [{"name": "Xanax"}]})
        self.put("plan.json", {"checked": MIDNIGHT - 60, "route": [{}]})
        self.put("travel.json", {"checked": NOW - 31 * 60})
        (self.d / "updates.csv").write_text(f"seen,country\n{NOW - 20 * 60},mex\n", encoding="utf-8")
        b = self.board()
        self.assertTrue(all(j.state == STALE for j in b.values()), {k: j.state for k, j in b.items()})
        self.assertIn("1 item(s) below minimum", b["Keep stocked"].detail)
        self.assertIn("before today's reset", b["City shop buys"].detail)
        self.assertIn("Built as v1.5.1", b["Torn script"].detail)

    def test_ago(self):
        self.assertEqual(ago(None, NOW), "never")
        self.assertEqual(ago(NOW - 30, NOW), "just now")
        self.assertEqual(ago(NOW - 45 * 60, NOW), "45 min ago")
        self.assertEqual(ago(NOW - 3 * 86400, NOW), "3 days ago")

    def test_last_csv_time_reads_only_the_tail(self):
        path = self.d / "big.csv"
        path.write_text("seen,country\n" + "".join(f"{i},mex\n" for i in range(100_000)), encoding="utf-8")
        self.assertEqual(last_csv_time(path), 99_999)
        self.assertIsNone(last_csv_time(self.d / "missing.csv"))


if __name__ == "__main__":
    unittest.main()
