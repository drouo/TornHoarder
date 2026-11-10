"""Runs the generated userscript's Add Listing decisions (planListing /
plannedLines) in Node against a stubbed page, so the rules that decide what
gets listed, at what price and quantity, are tested without a browser.
Skipped when Node isn't installed.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.userscript import build_userscript

NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const el = () => ({ style: {}, appendChild() {}, addEventListener() {}, contains() { return false; }, set textContent(v) {} });
global.window = { scrollTo() {}, scrollBy() {}, innerHeight: 800, scrollY: 0 };
window.top = window;
global.location = { pathname: "/page.php", search: "?sid=ItemMarket", hash: "#/addListing" };
global.document = { createElement: el, body: el(), querySelector() { return null; }, querySelectorAll() { return []; }, documentElement: { scrollHeight: 0 } };
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const L = window.__TH_LIST__;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = cases.map(c => c.lines ? L.plannedLines() : L.planListing(c.id, c.entry, c.rolls, c.isCopy, c.owned, c.shopHeld));
process.stdout.write(JSON.stringify(out));
"""

AXE_FIT = {"type": "Weapon", "intercept": 0.0, "coef": {"damage": 1.0, "accuracy": 1.0}, "base": {"damage": 50.0, "accuracy": 50.0}}
AXE_CURVE = [[0.0, 1000.0, 1100.0, 1200.0], [100.0, 3000.0, 3300.0, 3600.0]]

PRICE_MAP = {
    "180": {"name": "Beer", "plan": "dump", "list": 950, "qty": 5, "fast": 1000, "fair": 1100, "greedy": 1200},
    "1483": {"name": "Insulin", "plan": "shop", "list": 9000, "qty": 431, "fast": 8999, "fair": 17000, "greedy": 19000,
             "fallback": {"plan": "competitive", "list": 8999, "refMin": None}},
    "1485": {"name": "Shark Fin", "plan": "shop", "list": 66000, "qty": 3, "fast": 65000, "fair": 70000, "greedy": 72000,
             "shopMin": 69474, "fallback": {"plan": "competitive", "list": 65999, "refMin": None}},
    "1350": {"name": "ID Badge", "plan": "keep", "list": None, "qty": 0, "fast": 1, "fair": 1, "greedy": 1},
    "8": {"name": "Axe", "plan": "dump", "auto": True, "list": 1000, "fast": 1000, "fair": 1100, "greedy": 1200,
          "curve": AXE_CURVE, "fit": AXE_FIT, "copies": 3, "estimate": 3300, "keepMinQuality": 40.0, "keepAll": False},
    "438": {"name": "Cricket Bat", "plan": "shop", "auto": True, "list": 10000, "fast": 9000, "fair": 9500, "greedy": 9900,
            "copies": 32, "estimate": 320000, "shopMin": 10527, "fallback": {"plan": "competitive", "list": 9000, "refMin": None}},
    "999": {"name": "Cheap Thing", "plan": "dump", "list": 10, "qty": 4, "refMin": 500, "fast": 11, "fair": 12, "greedy": 13},
}
SHOP_RUN = {"route": [{"step": "pharmacy", "name": "Pharmacy", "items": 431, "payout": 3879000, "url": "u"}],
            "items": {"1483": {"qty": 431, "uids": None, "price": 9000, "step": "pharmacy", "name": "Insulin"},
                      "438": {"qty": 1, "uids": ["111"], "price": 10000, "step": "bigalgunshop", "name": "Cricket Bat"}}}


@unittest.skipUnless(NODE, "node not installed")
class ListModeDecisionsTest(unittest.TestCase):
    def run_cases(self, cases):
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript(PRICE_MAP, {}, shop_run=SHOP_RUN), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            (d / "c.json").write_text(json.dumps(cases), encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), str(d / "c.json")],
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_stacks_list_only_the_selling_quantity_capped_by_what_you_own(self):
        a, b = self.run_cases([
            {"id": "180", "entry": PRICE_MAP["180"], "rolls": None, "isCopy": False, "owned": 7},
            {"id": "180", "entry": PRICE_MAP["180"], "rolls": None, "isCopy": False, "owned": 3},
        ])
        self.assertEqual((a["price"], a["qty"]), (950, 5))
        self.assertEqual(b["qty"], 3)

    def test_keep_and_shop_routed_items_are_held(self):
        keep, shop = self.run_cases([
            {"id": "1350", "entry": PRICE_MAP["1350"], "rolls": None, "isCopy": False, "owned": 3},
            {"id": "1483", "entry": PRICE_MAP["1483"], "rolls": None, "isCopy": False, "owned": 431},
        ])
        self.assertEqual(keep["hold"], "Keep")
        self.assertIn("sell at Pharmacy", shop["hold"])

    def test_shop_item_no_shop_takes_lists_with_its_market_plan_without_shop_floor(self):
        (fin,) = self.run_cases([{"id": "1485", "entry": PRICE_MAP["1485"], "rolls": None, "isCopy": False, "owned": 3}])
        self.assertEqual((fin["price"], fin["qty"], fin["plan"]), (65999, 3, "competitive"))

    def test_weapon_copies_keep_best_price_from_rolls_and_top_for_high_rolls(self):
        kept, junk, great = self.run_cases([
            # fit: quality = (damage - 50) + (accuracy - 50)
            {"id": "8", "entry": PRICE_MAP["8"], "rolls": {"damage": 80, "accuracy": 70}, "isCopy": True, "owned": 1},
            {"id": "8", "entry": PRICE_MAP["8"], "rolls": {"damage": 55, "accuracy": 55}, "isCopy": True, "owned": 1},
            {"id": "8", "entry": dict(PRICE_MAP["8"], keepMinQuality=None), "rolls": {"damage": 80, "accuracy": 80},
             "isCopy": True, "owned": 1},
        ])
        self.assertEqual(kept["hold"], "keeping your best copy")       # quality 50 >= 40
        # quality 10, dump: curve fast at q10 = 1200, minus 5% = 1140
        self.assertEqual((junk["price"], junk["qty"], junk["plan"]), (1140, 1, "dump"))
        # quality 60 >= topMinQuality 50 -> top dollar: curve greedy at q60 = 1200 + 0.6 * 2400 = 2640
        self.assertEqual((great["price"], great["plan"]), (2640, "top"))

    def test_copy_never_lists_below_the_items_floor(self):
        # A thin quality band priced at $200 can't drag a copy under the item's
        # floor (fast $1000; dump lists 5% under: $950).
        cheap_band = dict(PRICE_MAP["8"], keepMinQuality=None, curve=[[0.0, 200.0, 220.0, 240.0], [100.0, 210.0, 230.0, 250.0]])
        (copy,) = self.run_cases([{"id": "8", "entry": cheap_band, "rolls": {"damage": 55, "accuracy": 55}, "isCopy": True, "owned": 1}])
        self.assertEqual((copy["price"], copy["plan"]), (950, "dump"))

    def test_only_the_routed_number_of_copies_is_held_for_the_shop(self):
        first, second = self.run_cases([
            {"id": "438", "entry": PRICE_MAP["438"], "rolls": None, "isCopy": True, "owned": 1, "shopHeld": 0},
            {"id": "438", "entry": PRICE_MAP["438"], "rolls": None, "isCopy": True, "owned": 1, "shopHeld": 1},
        ])
        self.assertTrue(first["shopHold"])
        # The other 31 Cricket Bats aren't going to the shop: market plan, no shop floor.
        self.assertEqual((second["price"], second["plan"]), (9000, "competitive"))

    def test_under_half_reference_price_is_held(self):
        (cheap,) = self.run_cases([{"id": "999", "entry": PRICE_MAP["999"], "rolls": None, "isCopy": False, "owned": 4}])
        self.assertIn("under half Torn's reference price", cheap["hold"])

    def test_review_list_skips_kept_and_shop_routed_items(self):
        (lines,) = self.run_cases([{"lines": True}])
        names = [l["name"] for l in lines]
        self.assertEqual(set(names), {"Beer", "Shark Fin", "Axe", "Cheap Thing", "Cricket Bat"})
        bat = next(l for l in lines if l["name"] == "Cricket Bat")
        self.assertEqual((bat["qty"], round(bat["value"])), (31, 310000))
        axe = next(l for l in lines if l["name"] == "Axe")
        self.assertEqual((axe["qty"], axe["copies"]), (3, True))


if __name__ == "__main__":
    unittest.main()
