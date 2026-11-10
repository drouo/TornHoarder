"""Tests for the item catalog refresh (src/tma/catalog.py).

conftest.py stubs `catalog.refresh_catalog_if_stale` for every test so the
app never touches the network or the real data/ files; this module binds
the real function at import time (before that fixture runs) and only ever
points it at tmp paths.
"""

import csv
import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.catalog import build_profiles, refresh_catalog_if_stale

HAMMER = {
    "id": 1,
    "name": "Hammer",
    "type": "Weapon",
    "sub_type": "Clubbing",
    "value": {"vendor": {"country": "Torn", "name": "Big Al's Gun Shop"}, "buy_price": 75, "sell_price": 50, "market_price": 40},
    "circulation": 2897193,
    "details": {"category": "Melee", "base_stats": {"damage": 17, "accuracy": 55, "armor": 0}},
}


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls += 1
        assert url.endswith("/torn/items"), url
        return FakeResponse(self.payload)


class BuildProfilesTest(unittest.TestCase):
    def test_keeps_shop_prices_and_vendor(self):
        prof = build_profiles([HAMMER])["1"]
        self.assertEqual((prof["sell_price"], prof["buy_price"], prof["vendor"]), (50, 75, "Big Al's Gun Shop"))
        self.assertEqual(prof["stats"], {"damage": 17.0, "accuracy": 55.0, "armor": 0.0})


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        d = Path(self._tmp.name)
        self.dict_path, self.prof_path = d / "dict.csv", d / "profiles.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_catalog_is_downloaded(self):
        sess = FakeSession({"items": [HAMMER]})
        self.assertEqual(refresh_catalog_if_stale(sess, "k", self.dict_path, self.prof_path), (True, None))
        self.assertEqual(json.loads(self.prof_path.read_text(encoding="utf-8"))["1"]["sell_price"], 50)
        with self.dict_path.open(encoding="utf-8-sig") as f:
            self.assertEqual(list(csv.DictReader(f)), [{"key": "Hammer", "id": "1"}])

    def test_fresh_catalog_is_left_alone(self):
        self.prof_path.write_text('{"1": {"crime_hint": false}}', encoding="utf-8")
        self.dict_path.write_text("key,id\n", encoding="utf-8")
        sess = FakeSession({"items": [HAMMER]})
        self.assertEqual(refresh_catalog_if_stale(sess, "k", self.dict_path, self.prof_path), (False, None))
        self.assertEqual(sess.calls, 0)

    def test_catalog_missing_a_newer_field_is_refreshed(self):
        # Written before crime_hint existed: fresh by age, but stale by shape.
        self.prof_path.write_text('{"1": {"name": "Hammer"}}', encoding="utf-8")
        self.dict_path.write_text("key,id\n", encoding="utf-8")
        refreshed, _ = refresh_catalog_if_stale(FakeSession({"items": [HAMMER]}), "k", self.dict_path, self.prof_path)
        self.assertTrue(refreshed)

    def test_stale_catalog_is_refreshed(self):
        self.prof_path.write_text("{}", encoding="utf-8")
        self.dict_path.write_text("key,id\n", encoding="utf-8")
        old = time.time() - 2 * 24 * 3600
        os.utime(self.prof_path, (old, old))
        refreshed, _ = refresh_catalog_if_stale(FakeSession({"items": [HAMMER]}), "k", self.dict_path, self.prof_path)
        self.assertTrue(refreshed)

    def test_api_error_keeps_existing_files(self):
        sess = FakeSession({"error": {"code": 2, "error": "Incorrect key"}})
        refreshed, err = refresh_catalog_if_stale(sess, "k", self.dict_path, self.prof_path)
        self.assertFalse(refreshed)
        self.assertIn("Incorrect key", err)
        self.assertFalse(self.prof_path.exists())


if __name__ == "__main__":
    unittest.main()
