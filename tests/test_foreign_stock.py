import csv
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.foreign_stock import StockLog, observations, run


def feed(seen, xanax, fossil=40):
    return {
        "sou": {"update": seen, "stocks": [{"id": 206, "name": "Xanax", "quantity": xanax, "cost": 750000}]},
        "arg": {"update": 1000, "stocks": [{"id": 300, "name": "Patagonian Fossil", "quantity": fossil, "cost": 1000}]},
    }


def rows(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class ForeignStockLogTest(unittest.TestCase):
    def test_flattens_the_feed(self):
        obs = observations(feed(1000, 300))
        self.assertEqual(obs[0], {"seen": 1000, "country": "sou", "item_id": 206, "item_name": "Xanax",
                                  "quantity": 300, "cost": 750000})

    def test_logs_only_changes_and_new_observations(self):
        with TemporaryDirectory() as d:
            stock, upd = Path(d) / "s.csv", Path(d) / "u.csv"
            log = StockLog(stock, upd)
            self.assertEqual(log.record(feed(1000, 300)), (2, 2))
            # Same stock, South Africa looked at again: no stock row, one observation.
            self.assertEqual(log.record(feed(1300, 300)), (0, 1))
            # Xanax sold out.
            self.assertEqual(log.record(feed(1600, 0)), (1, 1))
            self.assertEqual([(r["seen"], r["quantity"]) for r in rows(stock) if r["country"] == "sou"],
                             [("1000", "300"), ("1600", "0")])
            self.assertEqual([r["seen"] for r in rows(upd) if r["country"] == "sou"], ["1000", "1300", "1600"])

    def test_resumes_from_disk_without_duplicates(self):
        with TemporaryDirectory() as d:
            stock, upd = Path(d) / "s.csv", Path(d) / "u.csv"
            StockLog(stock, upd).record(feed(1000, 300))
            again = StockLog(stock, upd)  # e.g. after a restart
            self.assertEqual(again.record(feed(1000, 300)), (0, 0))
            self.assertEqual(len(rows(stock)), 2)

    def test_run_once_reports_errors_without_crashing(self):
        class Down:
            def get(self, *a, **k):
                import requests
                raise requests.ConnectionError("offline")
        said = []
        with TemporaryDirectory() as d:
            run(StockLog(Path(d) / "s.csv", Path(d) / "u.csv"), Down(), once=True, say=said.append)
        self.assertIn("YATA: offline", said[0])


if __name__ == "__main__":
    unittest.main()
