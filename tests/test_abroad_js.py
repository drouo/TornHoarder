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

# A fake shop page abroad: rows shaped like Torn's (item image, name button,
# price, a "stock" screen-reader label, the money box and its hidden twin, Buy).
HARNESS = r"""
const fs = require("fs");
const page = JSON.parse(process.argv[3]);
const made = [];
function el(extra) {
  const e = Object.assign({ style: {}, kids: [], textContent: "", className: "", attrs: {},
    appendChild(k) { this.kids.push(k); }, addEventListener(t, f) { this["on" + t] = f; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    dispatchEvent() {}, closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    click() {} }, extra || {});
  made.push(e);
  return e;
}
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.window = { scrollTo() {}, innerWidth: 0 };
window.top = window;
global.location = { pathname: "/page.php", search: "?sid=travel", hash: "", href: "" };
global.Event = function () {};
global.HTMLInputElement = { prototype: {} };
const pressed = [];
const rows = page.rows.map(r => {
  const box = el({ value: "18" }), hidden = el({ value: "18" });
  const buy = el({ click() { pressed.push({ id: r.id, qty: box.value, hidden: hidden.value }); } });
  const label = el({ textContent: "stock" });
  label.parentElement = { textContent: "stock" + r.stock };
  return el({
    querySelector(sel) {
      if (sel === "img") return el({ attrs: { src: "/images/items/" + r.id + "/medium.png" } });
      if (/itemNameButton/.test(sel)) return el({ textContent: r.name });
      if (/displayPrice/.test(sel)) return el({ textContent: "$" + r.cost.toLocaleString("en-US") });
      if (/type=hidden\]$/.test(sel)) return hidden;
      if (/input-money/.test(sel)) return box;
      if (/buyButton/.test(sel)) return buy;
      return null;
    },
    querySelectorAll: sel => (/srOnly/.test(sel) ? [label] : []),
  });
});
global.document = {
  createElement: () => el(), documentElement: { scrollHeight: 0 },
  body: el({ dataset: { abroad: "true", traveling: "false" }, textContent: "You have purchased " + page.bought + " / 18 items so far." }),
  getElementById: () => null,
  querySelector: s => (s === "#user-money" ? el({ attrs: { "data-money": String(page.wallet) } }) : null),
  querySelectorAll: s => (/row___/.test(s) ? rows : []),
  createTextNode: t => ({ textContent: t, kids: [] }),
};
global.GM_xmlhttpRequest = o => o.onload({ responseText: JSON.stringify(page.prices) });
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const text = e => [e.textContent].concat((e.kids || []).map(text)).join("\n");
const btn = made.find(e => e.id === "th-abroad-btn");
const out = { button: btn.textContent, panel: made.map(text).join("\n") };
if (page.click) { btn.onclick(); out.pressed = pressed; }
process.stdout.write(JSON.stringify(out));
"""

ROWS = [
    {"id": 261, "name": "Wolverine Plushie", "cost": 20, "stock": 1288},
    {"id": 206, "name": "Xanax", "cost": 775_563, "stock": 0},
    {"id": 1504, "name": "Quartz Point", "cost": 167_837, "stock": 823},
    {"id": 262, "name": "Hockey Stick", "cost": 400, "stock": 250},
]
PRICES = {"checked": 4102444800, "sell": {"261": 30_000, "206": 800_000, "1504": 160_000, "262": 380}}


@unittest.skipUnless(NODE, "node not installed")
class AbroadJsTest(unittest.TestCase):
    def run_page(self, **page):
        page = {"rows": ROWS, "prices": PRICES, "bought": 0, "wallet": 1_000_000, **page}
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_offers_the_most_profitable_item_in_stock(self):
        # Xanax pays most but is sold out; Quartz and Hockey Sticks lose money.
        out = self.run_page()
        self.assertTrue(out["button"].startswith("Buy 18 × Wolverine Plushie · $360"), out["button"])
        self.assertIn("Bag: 0 of 18 bought.", out["panel"])
        self.assertIn("· Wolverine Plushie: +$29,980 · 1,288 in stock", out["panel"])
        self.assertNotIn("· Quartz Point:", out["panel"])

    def test_fills_what_you_can_still_carry_and_presses_buy(self):
        out = self.run_page(bought=11, click=True)
        self.assertEqual(out["pressed"], [{"id": 261, "qty": "7", "hidden": "7"}])

    def test_wallet_limits_the_amount(self):
        rows = [{"id": 206, "name": "Xanax", "cost": 775_563, "stock": 40}]
        out = self.run_page(rows=rows, wallet=2_000_000)
        self.assertTrue(out["button"].startswith("Buy 2 × Xanax"), out["button"])

    def test_full_bag_says_fly_home(self):
        self.assertIn("Bag full ✓ fly home", self.run_page(bought=18)["button"])

    def test_without_prices_it_says_to_plan_first(self):
        out = self.run_page(prices={"checked": 0, "sell": {}})
        self.assertIn("click Plan a trip", out["panel"])


if __name__ == "__main__":
    unittest.main()
