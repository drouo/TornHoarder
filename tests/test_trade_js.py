"""The userscript on Torn's trade page and Item Market listings: what the
app's trade plan says to give each trader, and which listings to pull."""

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
const page = JSON.parse(process.argv[3]);
const made = [];
function el(extra) {
  const e = Object.assign({ style: {}, kids: [], textContent: "", className: "", attrs: {}, tagName: "DIV",
    appendChild(k) { this.kids.push(k); }, addEventListener(t, f) { this["on" + t] = f; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    dispatchEvent() {}, closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    click() {} }, extra || {});
  made.push(e);
  return e;
}
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem(k, v) { store[k] = String(v); }, removeItem(k) { delete store[k]; } };
global.window = { scrollTo() {}, innerWidth: 0 };
window.top = window;
global.location = { pathname: page.path, search: page.search || "", hash: page.hash, href: "" };
global.Event = function () {};
global.HTMLInputElement = { prototype: {} };
const addItems = el({ tagName: "A", textContent: "Add items" });
// Torn's "Start a trade" form: player box, description, INITIATE TRADE.
const clicked = [];
const desc = el({ tagName: "TEXTAREA", value: "" });
const form = el({ querySelector: s => (/textarea/.test(s) ? desc : null) });
const go = el({ tagName: "INPUT", type: "submit", value: "INITIATE TRADE", closest: s => (s === "form" ? form : null),
  click() { clicked.push("initiate"); } });
Object.assign(store, page.store || {});
// Torn's Add items page: one row per item you hold, an amount box each, ADD TO TRADE.
const amounts = (page.addRows || []).map(r => {
  const row = el({ tagName: "LI", textContent: r.name + " x" + r.have,
    querySelector: s => (/img/.test(s) && r.id ? el({ getAttribute: () => "/images/items/" + r.id + "/medium.png" }) : null) });
  return el({ tagName: "INPUT", name: "amount", value: "", closest: s => (s === "li" ? row : null), forItem: r.name });
});
const addBtn = el({ tagName: "INPUT", type: "submit", value: "ADD TO TRADE", click() { clicked.push("add"); } });
const acceptBtn = el({ className: "accept-trade-button" });
global.document = {
  createElement: () => el(), documentElement: { scrollHeight: 0 },
  body: el({ dataset: { abroad: "false", traveling: "false" }, textContent: page.bodyText || "" }),
  getElementById: () => null,
  querySelector: s => (s === "input[name=amount]" ? amounts[0] || null : s === ".accept-trade-button" && page.view ? acceptBtn : null),
  querySelectorAll: s => (/^input\[type=submit\], button$/.test(s) ? (page.start ? [go] : page.addRows ? [addBtn] : [])
    : s === "input[name=amount]" ? amounts
    : /button, a, input/.test(s) ? [addItems] : []),
  createTextNode: t => ({ textContent: t, kids: [] }), addEventListener() {},
};
const logs = [];
global.GM_xmlhttpRequest = o => {
  if (o.method === "POST") { if (/script-log$/.test(o.url)) logs.push(JSON.parse(o.data).msg); return; }
  if (o.onload) o.onload({ responseText: JSON.stringify(/trade-plan$/.test(o.url) ? page.plan : {}) });
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const text = e => [e.textContent].concat((e.kids || []).map(text)).join("\n");
const btn = made.find(e => e.id === "th-trade-btn");
if (page.skip) made.find(e => e.textContent && e.textContent.indexOf("✓ " + page.skip) === 0).onclick();
const before = btn ? btn.textContent : null;
for (let i = 0; i < (page.click === true ? 1 : page.click || 0); i++) btn.onclick();
const pill = made.find(e => e.id === "th-pull");
process.stdout.write(JSON.stringify({ before, clicked, desc: desc.value, hash: location.hash,
  amounts: Object.fromEntries(amounts.map(a => [a.forItem, a.value])), skips: store["tornhoarder.tradeSkip"] || null,
  button: btn ? btn.textContent : null, panel: made.map(text).join("\n"),
  pill: pill ? text(pill) : null, logs, tradeWith: store["tornhoarder.tradeWith"] || null }));
"""

PLAN = {"checked": 1, "traders": [{"id": 3878831, "name": "Shekels", "total": 1_512_642.0, "items": [
    {"id": 261, "name": "Wolverine Plushie", "qty": 354, "price": 4273.0}]}],
        "pull": [{"id": 261, "name": "Wolverine Plushie", "amount": 50, "price": 4299.0, "trader": "Shekels", "pays": 4273.0}]}


@unittest.skipUnless(NODE, "node not installed")
class TradeJsTest(unittest.TestCase):
    def run_page(self, **page):
        page = {"plan": PLAN, **page}
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_trade_page_lists_what_to_give_and_logs_its_layout(self):
        out = self.run_page(path="/trade.php", hash="#step=start&userID=3878831")
        self.assertEqual(out["button"], "Give Shekels 1 item(s) · $1,512,642 (click to close)")
        self.assertIn("✓ 354 × Wolverine Plushie at $4,273 = $1,512,642", out["panel"])
        self.assertEqual(out["tradeWith"], "3878831")
        self.assertTrue(any(m.startswith("[TornHoarder] layout trade start: <a> Add items") for m in out["logs"]), out["logs"])

    def test_a_trader_who_cant_trade_now_is_flagged_and_logged(self):
        plan = json.loads(json.dumps(PLAN))
        plan["traders"][0]["status"] = {"open": False, "status": "Traveling to Mexico", "last": "Online"}
        out = self.run_page(path="/trade.php", hash="#step=start&userID=3878831", plan=plan)
        self.assertEqual(out["button"], "⛔ Shekels can't trade now: Traveling to Mexico (click to close)")
        self.assertIn("Shekels can't accept a trade while traveling to Mexico (last action Online)", out["panel"])
        self.assertIn("[TornHoarder] trade: Shekels unavailable: Traveling to Mexico", out["logs"])

    def test_one_click_on_the_start_page_fills_the_date_and_initiates(self):
        out = self.run_page(path="/trade.php", hash="#step=start&userID=3878831", start=True, click=True)
        self.assertEqual(out["before"], "Start trade with Shekels: 1 item(s) · $1,512,642")
        self.assertEqual(out["clicked"], ["initiate"])
        self.assertRegex(out["desc"], r"^Trade \d{4}-\d{2}-\d{2}$")
        self.assertTrue(any("initiating with Shekels (1 item(s)), description filled" in m for m in out["logs"]), out["logs"])

    def test_add_items_fills_picked_amounts_then_adds_to_trade(self):
        plan = json.loads(json.dumps(PLAN))
        plan["traders"][0]["items"] += [{"id": 618, "name": "Stingray Plushie", "qty": 54, "price": 4990.0},
                                        {"id": 1485, "name": "Shark Fin", "qty": 30, "price": 64457.0}]
        rows = [{"id": 261, "name": "Wolverine Plushie", "have": 400}, {"name": "Stingray Plushie", "have": 60},
                {"id": 206, "name": "Xanax", "have": 3}]
        out = self.run_page(path="/trade.php", hash="#step=add&ID=13897881", plan=plan, addRows=rows,
                            store={"tornhoarder.tradeWith": "3878831"}, click=2)
        self.assertEqual(out["before"], "Fill 3 item(s) for Shekels")
        self.assertEqual(out["amounts"], {"Wolverine Plushie": "354", "Stingray Plushie": "54", "Xanax": ""})
        self.assertEqual(out["clicked"], ["add"])
        self.assertTrue(any("filled 2 of 3 item(s) from 3 rows; not found: Shark Fin" in m for m in out["logs"]), out["logs"])

    def test_trade_view_sends_you_to_add_whats_missing(self):
        out = self.run_page(path="/trade.php", hash="#step=view&ID=13897881", view=True, click=True,
                            store={"tornhoarder.tradeWith": "3878831"})
        self.assertEqual(out["before"], "Add 1 item(s) to the trade →")
        self.assertEqual(out["hash"], "#step=add&ID=13897881")
        done = self.run_page(path="/trade.php", hash="#step=view&ID=13897881", view=True, bodyText="Wolverine Plushie x354",
                             store={"tornhoarder.tradeWith": "3878831"})
        self.assertTrue(done["button"].startswith("All 1 item(s) are in: check the money from Shekels ($1,512,642)"), done["button"])

    def test_left_out_items_drop_from_the_trade_and_stay_out(self):
        plan = json.loads(json.dumps(PLAN))
        plan["traders"][0]["items"].append({"id": 618, "name": "Stingray Plushie", "qty": 54, "price": 4990.0})
        out = self.run_page(path="/trade.php", hash="#step=start&userID=3878831", plan=plan, start=True, skip="354")
        self.assertEqual(out["button"], "Start trade with Shekels: 1 item(s) · $269,460")
        self.assertEqual(json.loads(out["skips"]), {"261": True})
        again = self.run_page(path="/trade.php", hash="#step=start&userID=3878831", plan=plan, start=True,
                              store={"tornhoarder.tradeSkip": out["skips"]})
        self.assertIn("✕ 354 × Wolverine Plushie", again["panel"])

    def test_nothing_selected_never_initiates(self):
        out = self.run_page(path="/trade.php", hash="#step=start&userID=3878831", start=True, click=True,
                            store={"tornhoarder.tradeSkip": '{"261": true}'})
        self.assertEqual(out["before"], "Nothing selected to trade (click to close)")
        self.assertEqual(out["clicked"], [])

    def test_other_players_trades_have_nothing_planned(self):
        out = self.run_page(path="/trade.php", hash="#step=start&userID=1")
        self.assertTrue(out["button"].startswith("Trade: nothing planned"))

    def test_listings_page_says_which_to_pull(self):
        out = self.run_page(path="/page.php", search="?sid=ItemMarket", hash="#/viewListing")
        self.assertIn("· Wolverine Plushie ×50 at $4,299 → Shekels pays $4,273", out["pill"])


if __name__ == "__main__":
    unittest.main()
