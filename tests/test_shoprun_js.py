"""The end of a shop run, in Node against a stubbed shop page: the sales
recorded on earlier shop pages become a summary, and the done button goes on
to Add Listing. Skipped when Node isn't installed.
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
const made = [];
function el() {
  const e = { style: {}, kids: [], textContent: "", appendChild(k) { this.kids.push(k); }, addEventListener(t, f) { this["on" + t] = f; },
           setAttribute(k, v) { this["attr:" + k] = v; }, getAttribute(k) { return this["attr:" + k] || null; } };
  made.push(e);
  return e;
}
const store = JSON.parse(process.argv[3]);
global.localStorage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
global.window = { scrollTo() {} };
window.top = window;
global.location = { pathname: "/shops.php", search: "?step=pharmacy", hash: "", href: "" };
global.document = { createElement: el, body: Object.assign(el(), { dataset: JSON.parse(process.argv[4] || "{}") }),
                    querySelector() { return null }, querySelectorAll() { return []; }, title: "Pharmacy | TORN",
                    getElementById: () => null, addEventListener() {}, createTextNode: t => ({ textContent: t }) };
global.setInterval = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const btn = made.find(e => e.id === "tma-shop-btn");
const panel = made.find(e => e.id === "tma-shop-review");
const text = e => [e.textContent].concat(e.kids.map(text)).join("\n");
const dock = made.find(e => e.id === "th-dock");
const bar = dock.kids[0];
const before = { button: btn.textContent, panel: text(panel), shown: panel.style.display, dock: dock.style.cssText };
const tap = label => bar.kids.find(b => b.textContent === label).onclick();
tap("◀");
before.left = dock.style.cssText;
before.saved = store["tornhoarder.dock"];
tap("–");
before.folded = dock.kids[2].style.display;
tap("–");
btn.onclick();
process.stdout.write(JSON.stringify(Object.assign(before, { went: location.href, after: dock.style.cssText })));
"""

SHOP_RUN = {"route": [{"step": "pharmacy", "name": "Pharmacy", "items": 3, "payout": 27000, "url": "https://x/pharmacy"}],
            "items": {"1483": {"qty": 3, "uids": None, "price": 9000, "step": "pharmacy", "name": "Insulin"}}}


@unittest.skipUnless(NODE, "node not installed")
class ShopRunDoneTest(unittest.TestCase):
    def run_page(self, sold, body=None):
        with TemporaryDirectory() as d:
            d = Path(d)
            script = build_userscript({}, {}, shop_run=SHOP_RUN)
            start = script.index("var SHOP = ") + len("var SHOP = ")
            built = json.loads(script[start:script.index(";", start)])["built"]
            store = {"tornhoarder.shopRun": json.dumps({"built": built, "sold": sold})} if sold is not None else {}
            (d / "s.user.js").write_text(script, encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(store), json.dumps(body or {})],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_done_shows_what_sold_and_goes_on_to_add_listing(self):
        out = self.run_page([{"shop": "Pharmacy", "name": "Insulin", "qty": 3, "price": 9000}])
        self.assertEqual(out["button"], "Shop run done ✓ $27,000 sold · Next: list the rest →")
        self.assertEqual(out["shown"], "block")
        self.assertIn("Shop run sold 3 item(s) for $27,000:", out["panel"])
        self.assertIn("Pharmacy: 3 item(s) · $27,000", out["panel"])
        self.assertIn("3 × Insulin", out["panel"])
        self.assertEqual(out["went"], "https://www.torn.com/page.php?sid=ItemMarket#/addListing")
        # Clicking done also closes the dock, so the click visibly does something.
        self.assertIn("display:flex", out["dock"])
        self.assertIn("display:none", out["after"])

    def test_dock_moves_to_a_margin_folds_and_remembers(self):
        out = self.run_page([])
        self.assertIn("left:50%", out["dock"])  # narrow/unknown window: centered
        self.assertIn("left:12px", out["left"])
        self.assertEqual(out["saved"], "left")
        self.assertEqual(out["folded"], "none")

    def test_traveling_pauses_instead_of_walking_the_route(self):
        # Seen live: in the air every shop says "unavailable while you're traveling".
        out = self.run_page([], body={"traveling": "true", "abroad": "true", "country": "torn"})
        self.assertIn("You're traveling", out["button"])
        self.assertEqual(out["shown"], "none")
        self.assertEqual(out["went"], "")  # the click goes nowhere

    def test_a_previous_runs_sales_are_not_counted(self):
        # Nothing stored for this build: the summary says so instead of totalling an old run.
        out = self.run_page(None)
        self.assertEqual(out["button"], "Shop run done ✓ Next: list the rest →")
        self.assertIn("No sales were confirmed", out["panel"])


if __name__ == "__main__":
    unittest.main()
