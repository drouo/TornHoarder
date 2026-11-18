"""Listings: re-price suggestions with reasons (tma.my_listings) and the
userscript's fill on Torn's your-listings page."""

import json
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.autoupdate_server import build_server
from tma.my_listings import listing_plan, review_listing, write_listing_plan
from tma.userscript import build_userscript


def mine(price, amount=1, uid=None, quality=None, iid=110, name="Leather Bullwhip"):
    item = {"id": iid, "name": name, "uid": uid, "stats": {"quality": quality} if uid else None}
    return {"id": 77, "price": price, "amount": amount, "item": item}


def market(*prices, quality=50.0):
    return [{"price": float(p), "amount": 1, "quality": quality} for p in prices]


class ReviewWhyTest(unittest.TestCase):
    def test_every_suggestion_says_what_it_rests_on(self):
        r = review_listing(mine(1900, uid=1, quality=5.2), market(1360, 1400, 1500), ref_price=1000)
        self.assertEqual(r["New price"], 1359.0)
        self.assertEqual(r["Why"], "3 other listing(s) at quality 5+ · cheapest $1,360 · never under $500 (half market value)")

    def test_a_deep_cut_is_flagged_not_quietly_suggested(self):
        # Live: Bullwhips listed at $1,351+ while other sellers' cheapest is $555.
        r = review_listing(mine(1351, uid=1, quality=28.0), market(555, 600, 620), ref_price=700)
        self.assertEqual(r["New price"], 554.0)
        self.assertTrue(r["Big drop"])
        small = review_listing(mine(1000, uid=1, quality=28.0), market(990, 1000, 1100), ref_price=1000)
        self.assertFalse(small["Big drop"])
        under_value = review_listing(mine(1000, uid=1, quality=28.0), market(790, 900, 950), ref_price=1000)
        self.assertTrue(under_value["Big drop"])  # 789 < 80% of the $1,000 market value

    def test_a_dumper_isnt_the_price_to_match(self):
        r = review_listing(mine(1000, uid=1, quality=28.0), market(100, 950, 980, 1000))
        self.assertEqual((r["Cheapest other"], r["New price"]), (950.0, 949.0))
        self.assertIn("ignored 1 dumped under", r["Why"])

    def test_many_cheap_listings_are_the_market(self):
        # Live: a Bo Staff at $2,848 was told it "could ask up to $5k" because
        # the 35 others near $2.8k were all taken for dumpers.
        r = review_listing(mine(2848, uid=1, quality=14.0), market(*([2850] * 35 + [5000] * 35)))
        self.assertEqual((r["Cheapest other"], r["Status"]), (2850.0, "cheapest"))
        self.assertNotIn("ignored", r["Why"])

    def test_the_plan_carries_only_what_to_change(self):
        rows = [review_listing(mine(1351, uid=900, quality=28.0), market(555, 600, 620)),
                review_listing(mine(500, amount=5, iid=180, name="Beer"), market(900, 1000, 1100)),
                review_listing(mine(1000, amount=5, iid=181, name="Rum"), market(1000.5, 1001, 1002))]
        plan = listing_plan(rows)
        self.assertEqual([(p["name"], p["uid"], p["now"], p["new"], p["big"]) for p in plan],
                         [("Leather Bullwhip", "900", 1351.0, 554.0, True), ("Beer", None, 500.0, 899.0, False)])

    def test_the_plan_reaches_the_userscript(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "listing_plan.json"
            server = build_server(Path(d) / "x.user.js", port=0, listing_path=path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_address[1]}/listing-plan"
            try:
                self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()), {"checked": 0, "rows": []})
                write_listing_plan(path, [review_listing(mine(500, amount=5, iid=180, name="Beer"), market(900))], now=7)
                got = json.loads(urllib.request.urlopen(url, timeout=5).read())
                self.assertEqual((got["checked"], [r["new"] for r in got["rows"]]), (7, [899.0]))
            finally:
                server.shutdown()
                server.server_close()


NODE = shutil.which("node")

# Torn's your-listings page: one row per listing, its info button naming
# "itemInfo-<item>-<uid>" (0 for a stack), a visible price box and a hidden twin.
HARNESS = r"""
const fs = require("fs");
const page = JSON.parse(process.argv[3]);
const made = [];
function el(extra) {
  const e = Object.assign({ style: {}, kids: [], _t: "", className: "", attrs: {},
    // Like the DOM: setting textContent replaces the children.
    get textContent() { return this._t; }, set textContent(v) { this._t = v; this.kids = []; },
    appendChild(k) { this.kids.push(k); }, addEventListener(t, f) { this["on" + t] = f; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    dispatchEvent() {}, closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    click() {} }, extra || {});
  made.push(e);
  return e;
}
const rows = (page.rows || []).map(r => {
  const visible = { type: "text", value: r.price, dispatchEvent() {} };
  const hidden = { type: "hidden", value: r.price.replace(/,/g, "") };
  const row = { querySelectorAll: s => (s === "input.input-money" ? [visible, hidden] : []) };
  const info = { control: "wai-viewListing-itemInfo-" + r.id + "-" + r.uid, closest: () => row };
  return { info, visible, hidden };
});
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
global.window = { scrollTo() {}, innerWidth: 0 };
window.top = window;
global.location = { pathname: "/page.php", search: "?sid=ItemMarket", hash: "#/viewListing", href: "" };
global.Event = function () {};
global.document = {
  createElement: () => el(), body: el({ dataset: {} }), documentElement: { scrollHeight: 0 },
  getElementById: () => null, addEventListener() {}, createTextNode: t => ({ textContent: t, kids: [] }),
  querySelector: () => null,
  querySelectorAll(s) {
    const m = /aria-controls\$="([^"]+)"/.exec(s);
    return m ? rows.filter(r => r.info.control.endsWith(m[1])).map(r => r.info) : [];
  },
};
global.GM_xmlhttpRequest = o => { if (/listing-plan$/.test(o.url)) o.onload({ responseText: JSON.stringify(page.plan) }); };
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const btn = made.find(e => e.id === "th-reprice-btn");
const out = { before: btn ? btn.textContent : null };
const panel = made.filter(e => e.kids && e.kids.some(k => /Re-prices from/.test(k.textContent || "")))[0];
const lines = () => (panel ? panel.kids.map(k => k.textContent) : []);
out.lines = lines();
if (page.tick != null) { panel.kids[page.tick].onclick(); out.ticked = btn.textContent; }
if (btn) { btn.onclick(); out.after = btn.textContent; out.afterLines = lines(); }
out.prices = rows.map(r => [r.visible.value, r.hidden.value]);
process.stdout.write(JSON.stringify(out));
"""

PLAN = {"checked": 9_999_999_999, "rows": [
    {"item_id": 110, "uid": "900", "name": "Leather Bullwhip", "quality": 27.8, "now": 1351, "new": 554, "big": True,
     "why": "3 other listing(s)", "status": "undercut by $796"},
    {"item_id": 180, "uid": None, "name": "Beer", "quality": None, "now": 500, "new": 899, "big": False,
     "why": "3 other listing(s)", "status": "cheapest by 44%"},
    {"item_id": 224, "uid": "555", "name": "Swiss Army Knife", "quality": 15.0, "now": 2077, "new": 2050, "big": False,
     "why": "9 other listing(s)", "status": "undercut by $27"},
]}


@unittest.skipUnless(NODE, "node not installed")
class RepriceJsTest(unittest.TestCase):
    def run_page(self, **page):
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    ROWS = [{"id": 110, "uid": "900", "price": "1,351"}, {"id": 180, "uid": "0", "price": "450"},
            {"id": 180, "uid": "0", "price": "500"}]

    def test_fills_safe_ones_leaves_big_drops_and_says_whats_off_screen(self):
        out = self.run_page(rows=self.ROWS, plan=PLAN)
        self.assertEqual(out["before"], "Fill 2 new price(s)")  # the ⚠ Bullwhip isn't ticked
        self.assertIn("☐ Leather Bullwhip (q28): $1,351 → $554 ⚠ · undercut by $796 · 3 other listing(s)", out["lines"])
        # Beer: the stack at $500 (not the one at $450); the knife isn't on screen.
        self.assertEqual(out["prices"], [["1,351", "1351"], ["450", "450"], ["899", "899"]])
        self.assertIn("1 not on screen: scroll down so Torn shows them, then click again.", out["afterLines"])
        self.assertIn("✓ filled Beer: $500 → $899 · cheapest by 44% · 3 other listing(s)", out["afterLines"])
        self.assertEqual(out["after"], "Fill 1 new price(s) (1 filled)")

    def test_a_big_drop_fills_only_once_ticked(self):
        out = self.run_page(rows=self.ROWS, plan=PLAN, tick=1)
        self.assertEqual(out["ticked"], "Fill 3 new price(s)")
        self.assertEqual(out["prices"][0], ["554", "554"])

    def test_nothing_shows_without_a_plan(self):
        self.assertIsNone(self.run_page(rows=self.ROWS, plan={"checked": 0, "rows": []})["before"])


if __name__ == "__main__":
    unittest.main()
