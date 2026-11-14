import json
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.autoupdate_server import build_server
from tma.shop_flips import day_start, plan_route, reset_in_s, write_plan
from tma.userscript import build_userscript

MIDNIGHT = 20_406 * 86_400  # 2025-11-14 00:00 UTC
NOON = MIDNIGHT + 12 * 3600


class DailyLimitClockTest(unittest.TestCase):
    def test_the_day_runs_midnight_to_midnight_utc(self):
        self.assertEqual(day_start(NOON), MIDNIGHT)
        self.assertEqual(reset_in_s(NOON), 12 * 3600)
        self.assertEqual(day_start(MIDNIGHT), MIDNIGHT)

    def test_plan_route_is_one_stop_per_shop_stamped_with_the_day(self):
        buys = pd.DataFrame([
            {"Item": "Beer", "Shop": "Bits 'n' Bobs", "Buy": 60, "item_id": 180},
            {"Item": "Diamond Ring", "Shop": "Jewelry Store", "Buy": 30, "item_id": 52},
            {"Item": "Bottle of Sake", "Shop": "Bits 'n' Bobs", "Buy": 10, "item_id": 294},
        ])
        route = plan_route(buys, MIDNIGHT)
        self.assertEqual([s["label"] for s in route],
                         ["Bits 'n' Bobs: 60 × Beer, 10 × Bottle of Sake", "Jewelry Store: 30 × Diamond Ring"])
        self.assertIn("thbuy=180:60,294:10", route[0]["url"])
        self.assertTrue(route[0]["url"].endswith(f"&thchk={MIDNIGHT}"))

    def test_plan_reaches_the_userscript_through_the_local_server(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "shop_plan.json"
            server = build_server(Path(d) / "x.user.js", port=0, plan_path=path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_address[1]}/shop-plan"
            try:
                self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()), {"checked": 0, "route": []})
                write_plan(path, [{"label": "a", "url": "u"}], now=5)
                self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()),
                                 {"checked": 5, "route": [{"label": "a", "url": "u"}]})
            finally:
                server.shutdown()
                server.server_close()


NODE = shutil.which("node")

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
    click() {} });
  Object.assign(e, extra || {});
  made.push(e);
  return e;
}
const store = page.store || {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } };
global.window = { scrollTo() {}, innerWidth: 0 };
window.top = window;
global.location = Object.assign({ pathname: "/index.php", search: "", hash: "", href: "" }, page.location);
global.Event = function () {};
const realNow = Date.now;
Date.now = () => page.now;
let onClick = null;
global.document = {
  createElement: () => el(), body: el({ dataset: {} }), documentElement: { scrollHeight: 0 },
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener(t, f) { if (t === "click") onClick = f; },
  createTextNode: t => ({ textContent: t, kids: [] }),
};
global.GM_xmlhttpRequest = o => { if (/shop-plan$/.test(o.url)) o.onload({ responseText: JSON.stringify(page.plan || { route: [] }) }); };
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
if (page.buy) {
  // Pressing Torn's BUY with an amount in its box.
  const input = { value: String(page.buy) };
  const wrap = { querySelector: () => input };
  const button = { closest: s => (s === ".buy-act-wrap" ? wrap : null) };
  onClick({ target: { closest: s => (s === ".buy-act button" ? button : null) } });
}
const text = e => [e.textContent].concat((e.kids || []).map(text)).join("");
const pill = made.find(e => e.id === "th-city-badge");
const out = { pill: text(pill), store };
const kid = t => pill.kids.find(k => k.textContent && k.textContent.indexOf(t) === 0);
if (page.startPlan) { kid("Today's buys").onclick({ stopPropagation() {} }); out.went = location.href; out.run = JSON.parse(store["tornhoarder.restockRun"]); }
if (page.fixTo != null) {
  kid("fix").onclick({ stopPropagation() {} });
  pill.kids.find(k => k.type === "number").value = String(page.fixTo);
  kid("save").onclick({ stopPropagation() {} });
  out.after = text(pill);
}
process.stdout.write(JSON.stringify(out));
"""

NOON_MS = NOON * 1000


@unittest.skipUnless(NODE, "node not installed")
class CityBadgeJsTest(unittest.TestCase):
    def run_page(self, **page):
        page = {"now": NOON_MS, **page}
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_a_buy_press_on_a_city_shop_counts_and_the_badge_counts_down(self):
        out = self.run_page(location={"pathname": "/shops.php", "search": "?step=bitsnbob"}, buy=60)
        log = json.loads(out["store"]["tornhoarder.cityBuys"])
        self.assertEqual([q for _, q in log], [60])
        again = self.run_page(store=out["store"])
        self.assertIn("City shops: 40 of 100 left · resets in 12h 00m", again["pill"])

    def test_yesterdays_buys_dont_count_and_todays_plan_is_offered(self):
        store = {"tornhoarder.cityBuys": json.dumps([[NOON_MS - 86_400_000, 100]])}
        plan = {"checked": NOON, "route": [{"label": "Bits 'n' Bobs: 60 × Beer", "url": "u1"},
                                           {"label": "Jewelry Store: 40 × Ring", "url": "u2"}]}
        out = self.run_page(store=store, plan=plan, startPlan=True)
        self.assertIn("100 city-shop buys available", out["pill"])
        self.assertIn("Today's buys: 2 shop(s) →", out["pill"])
        self.assertEqual(out["went"], "u1")
        self.assertEqual(out["run"]["name"], "Today's buys")

    def test_fix_sets_todays_count(self):
        store = {"tornhoarder.cityBuys": json.dumps([[NOON_MS - 3_600_000, 30]])}
        out = self.run_page(store=store, fixTo=12)
        self.assertIn("88 of 100 left", out["after"])

    def test_buys_elsewhere_dont_count(self):
        out = self.run_page(location={"pathname": "/page.php", "search": "?sid=ItemMarket"})
        self.assertNotIn("tornhoarder.cityBuys", out["store"])


if __name__ == "__main__":
    unittest.main()
