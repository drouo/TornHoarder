"""Tests for the weapon/armor copy-stats cache (tma.inventory.fetch_item_stats_cached)."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from app.ui.diagnostics import stats_cache_table
from tma.inventory import fetch_item_stats_cached


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """torn/{uid}?selections=itemdetails; uids in `broken` get an API error."""

    def __init__(self, broken=()):
        self.calls = []
        self.broken = set(broken)

    def get(self, url, headers=None, params=None, timeout=None):
        uid = url.rsplit("/", 1)[-1]
        self.calls.append(uid)
        if uid in self.broken:
            return FakeResponse({"error": {"code": 6, "error": "Incorrect ID"}})
        stats = {"damage": 50.0, "accuracy": 55.0, "armor": None, "quality": float(uid[-2:])}
        return FakeResponse({"itemdetails": {"uid": int(uid), "stats": stats}})


class StatsCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "uid_stats.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_run_reads_nothing_from_torn(self):
        first = FakeSession()
        stats, errors, cached = fetch_item_stats_cached(first, "k", ["1001", "1002"], self.path)
        self.assertEqual((sorted(first.calls), cached, errors), (["1001", "1002"], 0, []))
        self.assertEqual(stats["1002"]["quality"], 2.0)

        second = FakeSession()
        stats, _, cached = fetch_item_stats_cached(second, "k", ["1001", "1002", "1003"], self.path)
        self.assertEqual((second.calls, cached), (["1003"], 2))
        self.assertEqual(stats["1001"]["quality"], 1.0)

    def test_failed_reads_are_not_cached(self):
        fetch_item_stats_cached(FakeSession(broken={"1001"}), "k", ["1001"], self.path)
        retry = FakeSession()
        stats, _, cached = fetch_item_stats_cached(retry, "k", ["1001"], self.path)
        self.assertEqual((retry.calls, cached, stats["1001"]["quality"]), (["1001"], 0, 1.0))

    def test_copy_without_rolls_is_cached_too(self):
        # A successful read whose stats are all None is still an answer.
        class NoRolls(FakeSession):
            def get(self, url, headers=None, params=None, timeout=None):
                self.calls.append(url.rsplit("/", 1)[-1])
                return FakeResponse({"itemdetails": {"stats": {"damage": None, "accuracy": None, "armor": None, "quality": None}}})

        fetch_item_stats_cached(NoRolls(), "k", ["1001"], self.path)
        again = FakeSession()
        _, _, cached = fetch_item_stats_cached(again, "k", ["1001"], self.path)
        self.assertEqual((again.calls, cached), ([], 1))

    def test_prune_drops_copies_you_no_longer_own(self):
        fetch_item_stats_cached(FakeSession(), "k", ["1001", "1002"], self.path)
        fetch_item_stats_cached(FakeSession(), "k", ["1002"], self.path, prune=True)
        self.assertEqual(list(json.loads(self.path.read_text(encoding="utf-8"))), ["1002"])

    def test_records_first_seen_and_item_id_without_leaking_them_into_stats(self):
        stats, _, _ = fetch_item_stats_cached(FakeSession(), "k", ["1001"], self.path, item_ids={"1001": 8}, now=1000.0)
        self.assertNotIn("first_seen", stats["1001"])
        self.assertNotIn("item_id", stats["1001"])
        # A later run keeps the original first_seen.
        fetch_item_stats_cached(FakeSession(), "k", ["1001", "1002"], self.path, item_ids={"1001": 8}, now=5000.0)
        cache = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((cache["1001"]["first_seen"], cache["1001"]["item_id"]), (1000.0, 8))
        self.assertEqual(cache["1002"]["first_seen"], 5000.0)

    def test_cache_table_names_items_and_sorts_longest_held_first(self):
        cache = {
            "1": {"quality": 10.0, "damage": 50.0, "accuracy": 50.0, "armor": None, "item_id": 8, "first_seen": 86400.0 * 9},
            "2": {"quality": 20.0, "damage": 51.0, "accuracy": 52.0, "armor": None, "item_id": 8, "first_seen": 0.0 + 86400},
            "3": {"quality": 30.0, "damage": None, "accuracy": None, "armor": 40.0},
        }
        df = stats_cache_table(cache, {"8": {"name": "Axe"}}, now=86400.0 * 10)
        self.assertEqual(list(df["uid"]), ["2", "1", "3"])
        self.assertEqual(list(df["Days held"][:2]), [9.0, 1.0])
        self.assertEqual((df["Item"][0], df["Item"][2]), ("Axe", "?"))


if __name__ == "__main__":
    unittest.main()
