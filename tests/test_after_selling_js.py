"""Deposit, Ipecac and buy modes of the userscript, in Node against stubbed
Torn pages. Skipped when Node isn't installed."""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.shops import buy_url
from tma.userscript import build_userscript

NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const page = JSON.parse(process.argv[3]);
const made = [];
function el(extra) {
  const e = Object.assign({ style: {}, kids: [], textContent: "", className: "", attrs: {},
    appendChild(k) { this.kids.push(k); }, addEventListener(t, f) { this["on" + t] = f; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    dispatchEvent() {}, closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    click() { clicks.push(this.label || this.id); } }, extra || {});
  made.push(e);
  return e;
}
const clicks = [];
const store = page.store || {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
global.window = { scrollTo() {}, innerWidth: 0 };
window.top = window;
global.location = Object.assign({ pathname: "/", search: "", hash: "", href: "" }, page.location);
global.Event = function () {};
global.HTMLInputElement = { prototype: {} };
Object.defineProperty(HTMLInputElement.prototype, "value", { set(v) { this._v = v; }, configurable: true });

const wallet = el({ attrs: { "data-money": String(page.wallet) } });
const go = el({ label: "DEPOSIT MONEY", textContent: "DEPOSIT MONEY", className: page.goDisabled ? "torn-btn disabled" : "torn-btn" });
const moneyInput = el({ label: "money", offsetParent: {} });
const donate = el({ querySelectorAll: s => (s === "button" ? [go] : []), querySelector: s => (/:not/.test(s) ? moneyInput : null) });
const ids = {};
(page.shopItems || []).forEach(it => {
  ids[it.id + "-name"] = el({ textContent: it.name });
  ids[it.id + "-stock"] = el({ textContent: it.stock });
  ids[it.id + "-price"] = el({ textContent: it.price });
});
global.document = {
  createElement: () => el(), body: el({ dataset: {} }), documentElement: { scrollHeight: 0 },
  getElementById: id => ids[id] || null,
  querySelector(s) {
    if (s === "#user-money") return wallet;
    const m = /buyAmount\[\]"\]\[id="(\d+)"\]/.exec(s);
    if (m && ids[m[1] + "-name"]) return el({ label: "amount " + m[1] });
    return null;
  },
  querySelectorAll(s) { return s === "div.donate" && page.donate ? [donate] : []; },
};
global.setInterval = () => 0;
global.setTimeout = f => f();
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const btn = made.find(e => /^th-.*-btn$/.test(e.id || ""));
const text = e => [e.textContent].concat(e.kids.map(text)).join("\n");
const dock = made.find(e => e.id === "th-dock");
const out = { button: btn && btn.textContent, panel: dock ? text(dock.kids[2].kids[0]) : "" };
if (page.click && btn) { btn.onclick(); out.after = btn.textContent; out.clicks = clicks; out.filled = moneyInput.value; }
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node not installed")
class AfterSellingTest(unittest.TestCase):
    def run_page(self, page, cfg=None, shop_run=None):
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}, cfg=cfg, shop_run=shop_run), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_deposit_is_wallet_minus_keep_and_one_click_fills_and_presses(self):
        page = {"location": {"pathname": "/factions.php"}, "wallet": 10_000_000, "donate": True, "click": True}
        out = self.run_page(page, cfg={"keepCash": 2_000_000})
        self.assertEqual(out["button"], "Deposit $8,000,000 to the faction vault")
        self.assertIn("keeping $2,000,000", out["panel"])
        self.assertEqual(out["clicks"], ["DEPOSIT MONEY"])

    def test_no_deposit_when_wallet_is_within_keep(self):
        page = {"location": {"pathname": "/factions.php"}, "wallet": 1_500_000, "donate": True, "click": True}
        out = self.run_page(page, cfg={"keepCash": 2_000_000})
        self.assertEqual(out["button"], "Nothing to deposit: wallet is within your $2,000,000 keep")
        self.assertEqual(out["clicks"], [])  # nothing filled or pressed

    def test_disabled_deposit_button_is_left_to_you(self):
        page = {"location": {"pathname": "/factions.php"}, "wallet": 5_000_000, "donate": True, "click": True, "goDisabled": True}
        out = self.run_page(page)
        self.assertIn("press DEPOSIT MONEY yourself", out["after"])

    def test_ipecac_only_after_choosing_it(self):
        self.assertIsNone(self.run_page({"location": {"pathname": "/item.php"}, "wallet": 0}).get("button"))

    def test_shop_run_summary_offers_deposit_and_ipecac_over_threshold(self):
        shop_run = {"route": [{"step": "pharmacy", "name": "Pharmacy", "items": 1, "url": "u"}], "items": {}}
        script = build_userscript({}, {}, shop_run=shop_run)
        built = json.loads(script[script.index("var SHOP = ") + 11:script.index(";", script.index("var SHOP = "))])["built"]
        sold = [{"shop": "Pharmacy", "name": "Xanax", "qty": 10, "price": 800_000}]
        page = {"location": {"pathname": "/shops.php", "search": "?step=pharmacy"}, "wallet": 9_000_000,
                "store": {"tornhoarder.shopRun": json.dumps({"built": built, "sold": sold})}}
        # Same build time is needed for the stored run to count: rebuild with it fixed.
        out = self.run_page(page, cfg={"keepCash": 1_000_000}, shop_run=dict(shop_run, built=built))
        self.assertIn("Deposit $8,000,000 to the faction vault (keeping $1,000,000)", out["panel"])
        self.assertIn("Take Ipecac Syrup", out["panel"])  # $8m sold ≥ the $5m default

    def test_buy_mode_reads_the_link_and_fills_one_item(self):
        page = {"location": {"pathname": "/shops.php", "search": "?step=bitsnbobs&thbuy=180:100,187:20"}, "wallet": 0,
                "shopItems": [{"id": 180, "name": "Bottle of Beer", "stock": "Alcohol (3,311 in stock)", "price": "$10"}],
                "click": True}
        out = self.run_page(page)
        self.assertEqual(out["button"], "Buy 100 × Bottle of Beer · $1,000")
        self.assertIn("item 187: 0 of 20 bought (not in stock here now)", out["panel"])
        self.assertIn("press BUY yourself", out["after"])  # stub page has no BUY button


class BuyUrlTest(unittest.TestCase):
    def test_links_carry_ids_and_quantities(self):
        self.assertEqual(buy_url("Bits 'n' Bobs", {180: 100, 187: 0}),
                         "https://www.torn.com/shops.php?step=bitsnbobs&thbuy=180:100")
        self.assertEqual(buy_url("Print Store", {}), "https://www.torn.com/shops.php?step=printstore")
        self.assertEqual(buy_url("Big Al's Gun Shop", {5: 3}), "https://www.torn.com/bigalgunshop.php?thbuy=5:3")
        self.assertIsNone(buy_url("Nikeh Sports", {106: 1}))


if __name__ == "__main__":
    unittest.main()
