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
from tma.profile import away_from_torn, equipped_value, item_image_url, total_money
import pandas as pd

from tma.shop_flips import daily_buy_plan, log_stock, restock_times, scan_flips, shop_values, stock_changes


class ProfileTest(unittest.TestCase):
    def test_away_from_torn_only_when_flying_or_abroad(self):
        flying = {"profile": {"status": {"state": "Traveling", "description": "Traveling to Mexico"}}}
        self.assertEqual(away_from_torn(flying), "Traveling to Mexico")
        self.assertEqual(away_from_torn({"profile": {"status": {"state": "Abroad"}}}), "Abroad")
        self.assertIsNone(away_from_torn({"profile": {"status": {"state": "Okay"}}}))
        self.assertIsNone(away_from_torn({"profile": None}))

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

    def test_resale_is_capped_at_what_the_item_actually_sells_for(self):
        # Seen live: Parachutes listed from $480m, sell near $447.6m, cost $450m in Nikeh Sports.
        shop = [{"shop": "Nikeh Sports", "item_id": 106, "item_name": "Parachute", "price": 450e6, "stock": 509, "default_stock": 0}]
        market = {106: {"item_id": 106, "price_1": 480e6, "amount_1": 14}}
        self.assertFalse(scan_flips(shop, market).empty)  # the listing alone looks like a $6m flip
        self.assertTrue(scan_flips(shop, market, {106: 447_588_888}).empty)
        rose = scan_flips(SHOP_ITEMS, MARKET, {1: 900}).iloc[0]
        self.assertEqual((rose["Sell at"], rose["Market value"]), (900, 900))

    def test_shop_values_rank_by_market_value_profit(self):
        items = [dict(SHOP_ITEMS[0], item_id=180, item_name="Bottle of Beer", price=10.0),
                 dict(SHOP_ITEMS[1], item_id=2, price=8000.0), dict(SHOP_ITEMS[2], item_id=3)]
        df = shop_values(items, {180: 923, 2: 53389})  # no market value for 3: left out
        self.assertEqual(list(df["Item"]), ["Cagoutte", "Bottle of Beer"])
        self.assertEqual(df.iloc[1]["Profit / unit"], round(923 * 0.95 - 10))
        self.assertEqual(df.iloc[1]["Margin %"], round(100 * (923 * 0.95 - 10) / 10, 1))

    def test_stock_changes_and_restock_times(self):
        a = {"shop": "Bits", "item_id": 180, "item_name": "Beer", "price": 10.0, "stock": 0, "default_stock": 5000}
        changed, restocked = stock_changes({}, [a])
        self.assertEqual((len(changed), restocked), (1, []))  # first check: logged, not a restock
        self.assertEqual(stock_changes({180: 0}, [a]), ([], []))
        changed, restocked = stock_changes({180: 0}, [dict(a, stock=2400)])
        self.assertEqual([r["stock"] for r in restocked], [2400])
        log = pd.DataFrame([dict(a, time="t1"), dict(a, time="t2", stock=2400), dict(a, time="t3", stock=900),
                            dict(a, time="t4", stock=3000), dict(a, item_id=9, time="t2", stock=50)])
        rises = restock_times(log, [180])
        self.assertEqual(list(rises["time"]), ["t2", "t4"])
        self.assertEqual(list(rises["from"]), [0, 900])

    def test_daily_plan_fills_the_cap_with_most_profit_per_item_then_backups(self):
        def row(item, per, worth, buy_at=10.0):
            return {"Item": item, "Shop": "S", "Buy at": buy_at, "Sell at": buy_at + per, "Profit / unit": per,
                    "Worth buying": worth, "In stock": worth}
        flips = pd.DataFrame([row("Glue", 500, 800), row("Parachute", 6_000_000, 14, 450e6),
                              row("Gone", 9_000_000, 0), row("Tyrosine", 19_000, 62), row("Mask", 422, 1000),
                              row("Rose", 204, 649)])
        buys, spare = daily_buy_plan(flips, cap=100, backups=2)
        self.assertEqual(list(buys["Item"]), ["Parachute", "Tyrosine", "Glue"])  # sold-out "Gone" skipped
        self.assertEqual(list(buys["Buy"]), [14, 62, 24])
        self.assertEqual(buys["Profit"].iloc[0], 84_000_000)
        # Glue's unused 776 back up the picks first, then the next item.
        self.assertEqual(list(spare["Item"]), ["Glue", "Mask"])
        self.assertEqual(list(spare["Buy"]), [776, 100])

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
