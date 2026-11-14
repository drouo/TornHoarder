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

# A fake travel agency: map pins pick the destination, and the confirm text
# above Travel names it, as on Torn's page.
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
global.location = { pathname: "/page.php", search: "?sid=travel&thfly=" + encodeURIComponent(page.fly), hash: "", href: "" };
global.Event = function () {};
const confirm = c => "Are you sure you want to travel to " + c + " on a personal flight?Do you want to travel to " + c +
  " on a personal flight? You will reach your destination in around 1 hour and 51 minutes.";
const log = [];
const travel = el({ textContent: "Travel", className: "torn-btn", click() { log.push("travel:" + root.selected); } });
const root = el({ selected: page.selected, querySelectorAll: s => (s === "button.torn-btn" ? [travel] : []) });
Object.defineProperty(root, "textContent", { get() { return confirm(this.selected); } });
const pins = {};
["Canada", "Argentina", "South Africa"].forEach(c => { pins[c] = el({ click() { log.push("pin:" + c); root.selected = c; } }); });
global.document = {
  createElement: () => el(), documentElement: { scrollHeight: 0 },
  body: el({ dataset: { abroad: "false", traveling: page.traveling ? "true" : "false" } }),
  getElementById: id => (id === "travel-root" ? root : null),
  querySelector: s => { const m = /aria-label="([^"]+)"/.exec(s); return m ? pins[m[1]] || null : null; },
  querySelectorAll: () => [], addEventListener() {}, createTextNode: t => ({ textContent: t, kids: [] }),
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const btn = made.find(e => e.id === "th-fly-btn");
const out = { buttons: [btn.textContent] };
for (let i = 0; i < (page.clicks || 0); i++) { btn.onclick(); out.buttons.push(btn.textContent); }
out.log = log;
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node not installed")
class FlyJsTest(unittest.TestCase):
    def run_page(self, **page):
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_first_click_picks_the_country_second_presses_travel(self):
        out = self.run_page(fly="Argentina", selected="Canada", clicks=2)
        self.assertEqual(out["buttons"][0], "Pick Argentina on the map")
        self.assertEqual(out["buttons"][1], "✈ Travel to Argentina (1 hour and 51 minutes)")
        self.assertEqual(out["log"], ["pin:Argentina", "travel:Argentina"])

    def test_never_presses_travel_for_another_country(self):
        out = self.run_page(fly="Mars", selected="Canada", clicks=3)
        self.assertEqual(out["buttons"][-1], "Can't find Mars on this page")
        self.assertEqual(out["log"], [])

    def test_names_with_spaces(self):
        out = self.run_page(fly="South Africa", selected="South Africa", clicks=1)
        self.assertEqual(out["log"], ["travel:South Africa"])

    def test_once_in_the_air_it_just_says_so(self):
        out = self.run_page(fly="Argentina", selected="Argentina", traveling=True)
        self.assertIn("On the way to Argentina", out["buttons"][0])


if __name__ == "__main__":
    unittest.main()
