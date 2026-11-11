"""Price history: one CSV row per priced item per run (tma.price_history),
including tracked-category items you don't own, which never reach the sell plan."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tma.price_history import append_history, history_rows, load_history, tracked_item_ids

APP = ROOT / "app" / "streamlit_app.py"

PROFILES = {
    "186": {"name": "Sheep Plushie", "type": "Plushie", "market_price": 485},
    "187": {"name": "Teddy Bear Plushie", "type": "Plushie", "market_price": 477},
    "999": {"name": "Untradable Plushie", "type": "Plushie", "market_price": 0},
    "206": {"name": "Xanax", "type": "Drug", "market_price": 800000},
    "1": {"name": "Hammer", "type": "Weapon", "category": "Melee", "market_price": 50},
}


class PriceHistoryUnitTest(unittest.TestCase):
    def test_tracked_ids_are_tradable_items_in_the_categories_minus_owned(self):
        self.assertEqual(tracked_item_ids(PROFILES, ["Plushie", "Melee"], exclude=[186]), [1, 187])
        self.assertEqual(tracked_item_ids(PROFILES, []), [])

    def test_rows_and_append_write_the_header_once(self):
        summary = pd.DataFrame([{
            "item_id": 206, "item_name": "Xanax", "floor_price": 790000.0, "fast_sell_price": 789999.0,
            "fair_price": 805000.0, "greedy_price": 820000.0, "num_listings": 2, "my_quantity": 0,
        }])
        long = pd.DataFrame([{"item_id": 206, "quantity": 3}, {"item_id": 206, "quantity": 4}])
        rows = history_rows(summary, long, PROFILES, now=0.0)
        self.assertEqual(rows[0]["time"], "1970-01-01T00:00:00")
        self.assertEqual((rows[0]["category"], rows[0]["units_listed"], rows[0]["ref_price"]), ("Drug", 7, 800000.0))
        with TemporaryDirectory() as d:
            path = Path(d) / "h.csv"
            append_history(path, rows)
            append_history(path, rows)
            df = load_history(path)
            self.assertEqual(len(df), 2)
            self.assertEqual(path.read_text(encoding="utf-8").count("item_id"), 1)

    def test_missing_file_loads_empty(self):
        self.assertTrue(load_history(Path("does-not-exist.csv")).empty)


OWNED_ID, TRACKED_ID = 186, 187


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_get(self, url, headers=None, params=None, timeout=None):
    if url.endswith("/user/inventory"):
        items = [{"id": OWNED_ID, "name": "Sheep Plushie", "amount": 4, "equipped": False, "uid": None, "faction_owned": False}]
        return FakeResponse({"inventory": {"items": items}, "_metadata": {"total": 1}})
    if "/itemmarket" in url:
        iid = int(url.split("/market/")[1].split("/")[0])
        return FakeResponse({"itemmarket": {
            "item": {"id": iid, "name": f"Item {iid}", "type": "Plushie", "average_price": 500},
            "listings": [{"price": 480, "amount": 10}, {"price": 500, "amount": 5}],
        }})
    if "/torn/" in url:
        return FakeResponse({"error": {"code": 2, "error": "not needed in this test"}})
    raise AssertionError(f"unexpected URL in test: {url}")


class PriceHistoryAppTest(unittest.TestCase):
    def test_run_records_owned_and_tracked_items_but_plans_only_owned(self):
        from app import paths

        at = AppTest.from_file(str(APP))
        at.run(timeout=30)
        [sell] = [m for m in at.multiselect if m.key == "sell_categories"]
        sell.set_value(["Plushie"]).run(timeout=30)
        [track] = [m for m in at.multiselect if m.key == "history_categories"]
        track.set_value(["Plushie"]).run(timeout=30)
        [key] = [t for t in at.text_input if t.key == "api_key_input"]
        key.set_value("dummy-key").run(timeout=30)
        with patch("requests.Session.get", _fake_get):
            [run] = [b for b in at.button if b.label == "Run"]
            run.click().run(timeout=60)
        self.assertEqual(list(at.exception), [], [str(e) for e in at.exception])

        self.assertEqual(list(at.session_state["analysis"]["df_summary"]["item_id"]), [OWNED_ID])
        hist = load_history(paths.PRICE_HISTORY_PATH)
        self.assertIn(OWNED_ID, set(hist["item_id"]))
        self.assertIn(TRACKED_ID, set(hist["item_id"]))
        owned = hist[hist["item_id"] == OWNED_ID].iloc[0]
        self.assertEqual((int(owned["my_qty"]), int(owned["units_listed"]), owned["category"]), (4, 15, "Plushie"))


if __name__ == "__main__":
    unittest.main()
