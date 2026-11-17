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
global.document = {
  createElement: () => el(), documentElement: { scrollHeight: 0 },
  body: el({ dataset: { abroad: "false", traveling: "false" }, textContent: "" }),
  getElementById: () => null, querySelector: () => null,
  querySelectorAll: s => (/button, a, input/.test(s) ? [addItems] : []),
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
const pill = made.find(e => e.id === "th-pull");
process.stdout.write(JSON.stringify({ button: btn ? btn.textContent : null, panel: made.map(text).join("\n"),
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
        self.assertIn("· 354 × Wolverine Plushie at $4,273 = $1,512,642", out["panel"])
        self.assertEqual(out["tradeWith"], "3878831")
        self.assertTrue(any(m.startswith("[TornHoarder] layout trade start: <a> Add items") for m in out["logs"]), out["logs"])

    def test_a_trader_who_cant_trade_now_is_flagged_and_logged(self):
        plan = json.loads(json.dumps(PLAN))
        plan["traders"][0]["status"] = {"open": False, "status": "Traveling to Mexico", "last": "Online"}
        out = self.run_page(path="/trade.php", hash="#step=start&userID=3878831", plan=plan)
        self.assertEqual(out["button"], "⛔ Shekels can't trade now: Traveling to Mexico (click to close)")
        self.assertIn("Shekels can't accept a trade while traveling to Mexico (last action Online)", out["panel"])
        self.assertIn("[TornHoarder] trade: Shekels unavailable: Traveling to Mexico", out["logs"])

    def test_other_players_trades_have_nothing_planned(self):
        out = self.run_page(path="/trade.php", hash="#step=start&userID=1")
        self.assertTrue(out["button"].startswith("Trade: nothing planned"))

    def test_listings_page_says_which_to_pull(self):
        out = self.run_page(path="/page.php", search="?sid=ItemMarket", hash="#/viewListing")
        self.assertIn("· Wolverine Plushie ×50 at $4,299 → Shekels pays $4,273", out["pill"])


if __name__ == "__main__":
    unittest.main()
