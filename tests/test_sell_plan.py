"""Tests for the sell-plan classifier (src/tma/sell_plan.py) and the Sell
plan tab's override bookkeeping (app/ui/sell_plan.py)."""

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))

import pandas as pd
from streamlit.testing.v1 import AppTest

from app.ui.sell_plan import item_plans, overrides_from_edit, plan_rows
from tma import config
from tma.crime_items import crime_hint, parse_oc_items
from tma.sell_plan import KEEP_ALL, MARKET_FEE, SellRules, auto_plan, decide, price_guards, reserve_for

RULES = SellRules()
HARNESS = Path(__file__).resolve().parent / "fixtures" / "render_results_harness.py"


def _decide(**kw):
    args = dict(qty=1, fast=None, fair=None, greedy=None, shop_price=None, num_listings=100, quality=None, rules=RULES)
    args.update(kw)
    return decide(**args)


class AutoPlanTest(unittest.TestCase):
    def test_cheap_item_is_dumped(self):
        self.assertEqual(auto_plan(500, 10, 100, None, RULES)[0], "dump")

    def test_valuable_liquid_item_is_competitive(self):
        self.assertEqual(auto_plan(25_000, 1, 100, None, RULES)[0], "competitive")

    def test_valuable_thin_market_holds_out(self):
        self.assertEqual(auto_plan(25_000, 1, 5, None, RULES)[0], "top")

    def test_cheap_thin_market_is_still_dumped(self):
        # Few listings only matters when the item is worth protecting.
        self.assertEqual(auto_plan(500, 1, 5, None, RULES)[0], "dump")

    def test_big_stack_of_cheap_items_is_competitive(self):
        self.assertEqual(auto_plan(5_000, 300, 100, None, RULES)[0], "competitive")

    def test_high_roll_copy_holds_out_even_if_cheap(self):
        self.assertEqual(auto_plan(3_000, 1, 100, 60.0, RULES)[0], "top")


class DecideTest(unittest.TestCase):
    def test_dump_lists_under_the_floor(self):
        d = _decide(qty=10, fast=999, fair=1_200)
        self.assertEqual((d.plan, d.price), ("dump", 949))
        self.assertAlmostEqual(d.proceeds, 949 * 10 * (1 - MARKET_FEE))

    def test_competitive_lists_at_floor_minus_one(self):
        d = _decide(fast=29_999, fair=31_000, greedy=33_000)
        self.assertEqual((d.plan, d.price), ("competitive", 29_999))

    def test_top_lists_at_greedy(self):
        d = _decide(fast=29_999, fair=31_000, greedy=33_000, num_listings=4)
        self.assertEqual((d.plan, d.price), ("top", 33_000))

    def test_switches_to_shop_when_shop_beats_market_after_tax(self):
        # market dump 95 * 0.95 = 90.25 < shop 92
        d = _decide(qty=4, fast=100, fair=100, shop_price=92)
        self.assertEqual((d.plan, d.price, d.proceeds), ("shop", 92, 368))

    def test_stays_on_market_when_it_pays_more(self):
        d = _decide(fast=100, fair=100, shop_price=50)
        self.assertEqual(d.plan, "dump")

    def test_top_is_never_switched_to_shop(self):
        d = _decide(fast=100, fair=100, greedy=150, quality=70.0, shop_price=500)
        self.assertEqual(d.plan, "top")

    def test_override_wins_and_skips_shop_switch(self):
        # No switch to Shop, but the price is lifted to the shop's net: ceil(500 / 0.95).
        d = _decide(fast=100, fair=100, greedy=150, shop_price=500, override="competitive")
        self.assertEqual((d.plan, d.price, d.auto), ("competitive", 527, False))

    def test_keep_lists_nothing(self):
        d = _decide(qty=3, fast=100, fair=100, override="keep")
        self.assertEqual((d.plan, d.price, d.proceeds), ("keep", None, 0.0))

    def test_shop_override_without_shop_price_is_flagged(self):
        d = _decide(fast=100, fair=100, override="shop")
        self.assertEqual((d.plan, d.price), ("shop", None))
        self.assertIn("no shop", d.reason)


class PriceGuardTest(unittest.TestCase):
    def test_never_lists_below_what_a_shop_pays(self):
        # Your override skips the auto Shop switch, but the price is still lifted
        # to the shop's price grossed up for tax: ceil(92 / 0.95) = 97.
        d = _decide(fast=60, fair=100, shop_price=92, override="dump")
        self.assertEqual((d.plan, d.price), ("dump", 97))

    def test_auto_plan_far_under_reference_is_held(self):
        d = _decide(qty=10, fast=1, fair=1, ref_price=800_000)
        self.assertEqual((d.plan, d.price, d.proceeds), ("keep", None, 0.0))
        self.assertIn("check the market", d.reason)

    def test_override_far_under_reference_is_listed_with_warning(self):
        d = _decide(fast=1, fair=1, ref_price=800_000, override="dump")
        self.assertEqual((d.plan, d.price), ("dump", 1))
        self.assertIn("reference", d.reason)

    def test_normal_price_passes_the_reference_check(self):
        d = _decide(fast=29_999, fair=31_000, ref_price=32_000)
        self.assertEqual((d.plan, d.price), ("competitive", 29_999))

    def test_guards(self):
        self.assertEqual(price_guards(95, 1_000), (100, 500))
        self.assertEqual(price_guards(None, 0), (None, None))


class SellCategoryTest(unittest.TestCase):
    def test_item_outside_sell_categories_is_kept(self):
        d = _decide(qty=40, fast=800_000, fair=820_000, sellable=False)
        self.assertEqual((d.plan, d.price, d.proceeds), ("keep", None, 0.0))
        self.assertIn("sell categories", d.reason)

    def test_your_override_still_wins(self):
        d = _decide(fast=800_000, fair=820_000, sellable=False, override="competitive")
        self.assertEqual((d.plan, d.price), ("competitive", 800_000))

    def test_rules_round_trip_through_json_shape(self):
        r = SellRules(sell_categories=("Primary", "Melee"))
        self.assertEqual(r.to_dict()["sell_categories"], ["Primary", "Melee"])
        self.assertEqual(SellRules.from_dict(r.to_dict()), r)

    def test_defaults_never_sell_consumables(self):
        for cat in ("Drug", "Medical", "Booster", "Energy Drink", "Supply Pack", "Book", "Temporary"):
            self.assertNotIn(cat, SellRules().sell_categories)


class ShopFallbackTest(unittest.TestCase):
    def test_shop_item_carries_its_market_plan_for_the_add_listing_page(self):
        summary = pd.DataFrame([{"item_id": 1483, "my_quantity": 431, "fast_sell_price": 8999.0, "fair_price": 17298.0,
                                 "greedy_price": 19000.0, "num_clean_listings": 100}])
        edited = pd.DataFrame([{"item_id": 1483, "is_stat": False, "List price": 9000, "Qty": 431,
                                "Keeping": 0, "Selling": 431, "quality": None}])
        profiles = {"1483": {"id": 1483, "name": "Insulin", "type": "Other", "vendor": "Pharmacy", "sell_price": 9000}}
        plan = item_plans(summary, edited, profiles, RULES, {})[1483]
        self.assertEqual(plan["plan"], "shop")
        # Not sold to the Pharmacy -> its market plan (a $7.4M stack, so Competitive
        # at floor - $1), with no shop floor applied.
        self.assertEqual(plan["fallback"]["plan"], "competitive")
        self.assertEqual(plan["fallback"]["list"], 8999)


OC = {"1350": {"name": "ID Badge", "consumed": True, "crimes": ["Pet Project", "Market Forces", "Bidding War"]},
      "1203": {"name": "Lockpicks", "consumed": False, "crimes": ["Smoke and Wing Mirrors"]}}


STOCK = SellRules(keep_all_crime_items=False)


class ReserveTest(unittest.TestCase):
    def test_crime_items_are_all_kept_by_default(self):
        self.assertEqual(reserve_for(1350, {}, "Material", RULES, OC),
                         (KEEP_ALL, "crime item (OC: Pet Project, Market Forces…)"))
        self.assertEqual(reserve_for(906, {"crime_hint": True}, "Tool", RULES, {}), (KEEP_ALL, "crime item (used in crimes)"))

    def test_with_keep_all_off_oc_consumables_keep_three_tools_keep_one(self):
        self.assertEqual(reserve_for(1350, {}, "Material", STOCK, OC)[0], 3)
        self.assertEqual(reserve_for(1203, {}, "Tool", STOCK, OC)[0], 1)
        self.assertIn("needed for OC: Pet Project, Market Forces…", reserve_for(1350, {}, "Material", STOCK, OC)[1])

    def test_crime_items_and_keep_one_categories(self):
        self.assertEqual(reserve_for(906, {"crime_hint": True}, "Tool", STOCK, {}), (1, "used in crimes"))
        self.assertEqual(reserve_for(400, {}, "Clothing", RULES, {}), (1, "keep 1 of each Clothing"))
        self.assertEqual(reserve_for(400, {}, "Other", RULES, {}), (0, ""))

    def test_your_number_wins(self):
        self.assertEqual(reserve_for(1350, {}, "Material", RULES, OC, override=0), (0, "your choice"))
        self.assertEqual(reserve_for(400, {}, "Other", RULES, {}, override=20), (20, "your choice"))


class CrimeHintTest(unittest.TestCase):
    def test_current_crimes_flagged_retired_ones_not(self):
        self.assertTrue(crime_hint("A handkerchief. Required for the disposal of firearms in Disposal."))
        self.assertTrue(crime_hint("Improves Disposal crime exp & skill gains by 5%."))
        self.assertFalse(crime_hint("A very simple virus. Used to plant a virus in Crimes 1.0."))
        self.assertFalse(crime_hint("The most famous cow of Torn, punisher of the scammer."))

    def test_parse_organized_crimes(self):
        data = {"organizedcrimes": [
            {"name": "Pet Project", "difficulty": 1, "slots": [
                {"required_item": {"id": 1350, "name": "ID Badge", "is_used": True}},
                {"required_item": None}]},
            {"name": "Bidding War", "difficulty": 6, "slots": [
                {"required_item": {"id": 1350, "name": "ID Badge", "is_used": True}}]}]}
        self.assertEqual(parse_oc_items(data)["1350"],
                         {"name": "ID Badge", "consumed": True, "crimes": ["Pet Project", "Bidding War"], "min_level": 1, "max_level": 6})


def _row(iid, name, qty, is_stat=False, uid=None):
    return {"item_id": iid, "Item": name, "My quantity": qty, "Fast-sell price": 1000.0, "Fair price": 1100.0,
            "Greedy price": 1200.0, "is_stat": is_stat, "uid": uid}


class PlanRowsReserveTest(unittest.TestCase):
    PROFILES = {"624": {"id": 624, "type": "Clothing"}, "1350": {"id": 1350, "type": "Material"},
                "8": {"id": 8, "type": "Weapon", "category": "Melee"}}

    def test_stacks_sell_only_the_surplus(self):
        rows = plan_rows([_row(624, "Bikini", 7), _row(1350, "ID Badge", 40)], {}, {}, self.PROFILES, RULES, {}, oc_items=OC)
        bikini, badge = rows
        self.assertEqual((bikini["Keeping"], bikini["Selling"]), (1, 6))
        self.assertIn("keeping 1 (keep 1 of each Clothing)", bikini["Why"])
        # Crime items: all kept by default, however many you have.
        self.assertEqual((badge["Keeping"], badge["Selling"], badge["Plan"]), (40, 0, "Keep"))
        self.assertIn("keeping all 40: crime item (OC:", badge["Why"])
        # With keep-all off, only the stock of 3 stays.
        badge = plan_rows([_row(1350, "ID Badge", 40)], {}, {}, self.PROFILES, STOCK, {}, oc_items=OC)[0]
        self.assertEqual((badge["Keeping"], badge["Selling"]), (3, 37))

    def test_weapons_keep_the_best_copies(self):
        rows_out = [_row(8, "Axe", 1, True, "a"), _row(8, "Axe", 1, True, "b"), _row(8, "Axe", 1, True, "c")]
        rows = plan_rows(rows_out, {0: 10.0, 1: 40.0, 2: 25.0}, {}, self.PROFILES, RULES, {}, reserves={8: 1})
        self.assertEqual([r["Keeping"] for r in rows], [0, 1, 0])
        self.assertEqual(rows[1]["Plan"], "Keep")
        edited = pd.DataFrame(rows)
        summary = pd.DataFrame([{"item_id": 8, "my_quantity": 3, "fast_sell_price": 1000.0, "fair_price": 1100.0,
                                 "greedy_price": 1200.0, "num_clean_listings": 100}])
        plan = item_plans(summary, edited, self.PROFILES, RULES, {})[8]
        # The Add Listing page holds rows as good as the kept copy (quality 40).
        self.assertEqual((plan["keepMinQuality"], plan["keepAll"]), (40.0, False))


class SellRulesTest(unittest.TestCase):
    def test_from_dict_ignores_unknown_keys(self):
        r = SellRules.from_dict({"dump_undercut_pct": 10, "bogus": 1})
        self.assertEqual(r.dump_undercut_pct, 10)


class OverridesFromEditTest(unittest.TestCase):
    def _frames(self, plans_before, plans_after, autos):
        base = pd.DataFrame({"item_id": [26, 26], "auto_plan": autos, "Plan": plans_before})
        edited = base.assign(Plan=plans_after)
        return base, edited

    def test_untouched_copy_row_does_not_undo_another_copys_override(self):
        # Two copies of one weapon: copy 0 auto-Top (high roll), copy 1 auto-Dump.
        # Setting copy 1 to "Top dollar" must stick even though copy 0's row,
        # unchanged, also reads "Top dollar" == its own auto plan.
        base, edited = self._frames(["Top dollar", "Dump"], ["Top dollar", "Top dollar"], ["top", "dump"])
        self.assertEqual(overrides_from_edit(base, edited, {}), {26: "top"})

    def test_choosing_auto_plan_clears_override(self):
        base, edited = self._frames(["Keep", "Keep"], ["Keep", "Dump"], ["dump", "dump"])
        self.assertEqual(overrides_from_edit(base, edited, {26: "keep"}), {})


class SellPlanTabAppTest(unittest.TestCase):
    def test_default_render_auto_plans_and_writes_nothing(self):
        at = AppTest.from_file(str(HARNESS))
        at.run(timeout=30)
        self.assertEqual(list(at.exception), [], msg=[str(e) for e in at.exception])
        # Harness: Kevlar Gloves fast 850 vs shop 1,000 -> Shop; the AK-47 copy
        # (quality 30, ~$1.3k) -> Dump.
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual(metrics["Shop (1)"], "$3k")
        self.assertIn("Dump (1)", metrics)
        self.assertEqual(config.load_sell_prefs(), ({}, {}))

    def test_saved_override_is_applied(self):
        config.save_sell_prefs(SellRules().to_dict(), {641: "keep"})
        at = AppTest.from_file(str(HARNESS))
        at.run(timeout=30)
        self.assertEqual(list(at.exception), [])
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual(metrics["Keep (1)"], "—")


if __name__ == "__main__":
    unittest.main()
