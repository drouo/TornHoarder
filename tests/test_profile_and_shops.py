"""Profile card data (tma.profile), loadout values, and city-shop flips (tma.shop_flips)."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from app.ui.profile_card import loadout_rows
from tma.profile import equipped_value, item_image_url, total_money
from tma.shop_flips import log_stock, scan_flips


class ProfileTest(unittest.TestCase):
    def test_money_handles_plain_and_nested_amounts(self):
        m = total_money({"wallet": 1000, "vault": 500, "city_bank": {"amount": 2000, "profit": 9}, "faction": {"money": 300, "points": 4},
                         "points": 10, "daily_networth": 99999}, point_price=30000.0)
        self.assertEqual((m["cash_total"], m["points_value"], m["networth"]), (3800.0, 300000.0, 99999.0))
        self.assertIsNone(total_money({"points": 1})["points_value"])

    def test_copy_is_valued_at_its_quality_else_item_price_else_reference(self):
        summary = {"fair_price": 5000.0, "fast_sell_price": 4000.0,
                   "copy_tool": {"points": [{"q": 30.0, "n": 3, "fast": 6000, "fair": 7000, "greedy": 8000},
                                            {"q": 40.0, "n": 3, "fast": 9000, "fair": 10000, "greedy": 11000}]}}
        copy = {"id": 223, "uid": 1, "stats": {"damage": 69.0, "accuracy": 55.0, "armor": None, "quality": 37.2}}
        # Q37.2 interpolates between the Q30 (7000) and Q40 (10000) bands.
        self.assertEqual(equipped_value(copy, summary), 9160.0)
        self.assertEqual(equipped_value({"id": 5, "stats": None}, {"fair_price": 5000.0}), 5000.0)
        self.assertEqual(equipped_value({"id": 5}, None, ref_price=1234), 1234.0)
        self.assertIsNone(equipped_value({"id": 5}, None))

    def test_loadout_rows_are_in_slot_order_with_values_by_uid(self):
        eq = [{"id": 656, "uid": 9, "name": "Riot Body", "type": "Armor", "slot": 4, "stats": {"armor": 40.0, "quality": 22.0}},
              {"id": 223, "uid": 1, "name": "Jackhammer", "type": "Weapon", "slot": 1, "stats": {"damage": 69.0},
               "bonuses": [{"title": "Poison", "value": 86}]}]
        rows = loadout_rows(eq, {"1": 7000.0})
        self.assertEqual([r["Slot"] for r in rows], ["Primary", "Body"])
        self.assertEqual((rows[0]["Worth today"], rows[0]["Bonuses"], rows[1]["Worth today"]), (7000.0, "Poison 86%", None))
        self.assertEqual(rows[0]["Image"], item_image_url(223))


SHOP_ITEMS = [
    {"shop": "Bits 'n' Bobs", "item_id": 1, "item_name": "Single Red Rose", "price": 175.0, "stock": 288, "default_stock": 1000},
    {"shop": "Docks", "item_id": 2, "item_name": "Cagoutte", "price": 8000.0, "stock": 0, "default_stock": 100},
    {"shop": "Big Al's", "item_id": 3, "item_name": "Crowbar", "price": 400.0, "stock": 500, "default_stock": 1000},
]
MARKET = {
    1: {"item_id": 1, "price_1": 1450, "amount_1": 10, "price_2": 1500, "amount_2": 5},
    2: {"item_id": 2, "price_1": 53988, "amount_1": 2},
    3: {"item_id": 3, "price_1": 380, "amount_1": 50},
}


class ShopFlipsTest(unittest.TestCase):
    def test_only_profitable_items_ranked_by_what_will_actually_sell(self):
        df = scan_flips(SHOP_ITEMS, MARKET)
        self.assertEqual(list(df["Item"]), ["Single Red Rose", "Cagoutte"])
        rose = df[df["Item"] == "Single Red Rose"].iloc[0]
        # (1450 - 1) * 0.95 - 175 = 1201.55 a unit; 288 in stock but only 15 listed for sale.
        self.assertEqual((rose["Sell at"], rose["Profit / unit"], rose["Worth buying"]), (1449.0, 1202, 15))
        self.assertEqual((rose["Profit"], rose["Cost"]), (round(1201.55 * 15), 2625))
        # Sold out: nothing worth buying now, but still shown for after a restock.
        cag = df[df["Item"] == "Cagoutte"].iloc[0]
        self.assertEqual((cag["In stock"], cag["Worth buying"], cag["Profit"]), (0, 0, 0))

    def test_stock_log_appends_with_one_header(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "s.csv"
            log_stock(path, SHOP_ITEMS, "t1")
            log_stock(path, SHOP_ITEMS, "t2")
            text = path.read_text(encoding="utf-8")
            self.assertEqual((text.count("item_id"), text.count("\nt2,")), (1, 3))


class ProfileAppTest(unittest.TestCase):
    def test_card_and_priced_loadout_render_after_a_run(self):
        from streamlit.testing.v1 import AppTest

        from test_price_history import APP, _fake_get

        bundle = {
            "profile": {"id": 3653623, "name": "TheOldFarmer", "level": 57, "rank": "Outstanding", "image": "https://x/p.png",
                        "status": {"description": "Okay", "color": "green"}, "last_action": {"relative": "now"}},
            "equipment": [{"id": 187, "uid": None, "name": "Teddy Bear Plushie", "type": "Plushie", "slot": 1, "stats": None}],
            "bars": {"life": {"current": 10, "maximum": 10}, "energy": {"current": 750, "maximum": 150}},
            "money": {"wallet": 1000, "vault": 0, "points": 2, "daily_networth": 5},
            "errors": [],
        }
        with patch("tma.profile.fetch_profile_bundle", lambda *a, **k: bundle), patch("requests.Session.get", _fake_get):
            at = AppTest.from_file(str(APP))
            at.session_state["api_key"] = "profile-test-key"  # profile_bundle is cached per key
            at.run(timeout=30)
            [sell] = [m for m in at.multiselect if m.key == "sell_categories"]
            sell.set_value(["Plushie"]).run(timeout=30)
            [run] = [b for b in at.button if b.label == "Run"]
            run.click().run(timeout=60)
        self.assertEqual(list(at.exception), [], [str(e) for e in at.exception])
        self.assertTrue(any("TheOldFarmer" in m.value for m in at.markdown))
        # The equipped plushie isn't in the inventory; it's priced from the extra fetch (fair of 480/500 listings).
        self.assertGreater(at.session_state["analysis"]["loadout_values"]["187"], 0)
        self.assertEqual(at.session_state["analysis"]["money"]["cash"]["wallet"], 1000.0)


if __name__ == "__main__":
    unittest.main()
