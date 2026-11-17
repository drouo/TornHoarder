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
from tma.foreign_stock import recent_restocks
from tma.trips import load_buys, record_buy, trip_table
from tma.userscript import build_userscript

T = 1_794_600_000


def buy(ts, country="mexico", item_id=261, name="Wolverine Plushie", qty=18, cost=20, sell=30_000):
    return {"ts": ts, "country": country, "item_id": item_id, "item_name": name, "qty": qty, "cost": cost, "sell": sell}


class TripLogTest(unittest.TestCase):
    def test_buys_group_into_trips_newest_first(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "trip_buys.csv"
            record_buy(path, buy(T, qty=10))
            record_buy(path, buy(T + 60, item_id=262, name="Hockey Stick", qty=8, cost=400, sell=500))
            record_buy(path, buy(T + 6 * 3600, country="can", item_id=196, name="Insulin", qty=18, cost=1000, sell=2000))
            df = trip_table(load_buys(path), sell_now={261: 25_000, 262: 500})
        self.assertEqual(list(df["Country"]), ["Canada", "Mexico"])
        mex = df.iloc[1]
        self.assertEqual(mex["Bought"], "10 × Wolverine Plushie, 8 × Hockey Stick")
        self.assertEqual(mex["Spent"], 10 * 20 + 8 * 400)
        self.assertEqual(mex["Expected profit"], 10 * 30_000 + 8 * 500 - mex["Spent"])
        self.assertEqual(mex["Profit at today's prices"], 10 * 25_000 + 8 * 500 - mex["Spent"])
        self.assertTrue(pd.isna(df.iloc[0]["Profit at today's prices"]))

    def test_bad_records_are_refused(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "trip_buys.csv"
            with self.assertRaises(ValueError):
                record_buy(path, buy(T, qty=0))
            with self.assertRaises(KeyError):
                record_buy(path, {"ts": T})
            self.assertEqual(load_buys(path), [])

    def test_server_takes_buys_and_serves_restocks(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            trips, stock = d / "trip_buys.csv", d / "foreign_stock.csv"
            server = build_server(d / "x.user.js", port=0, trips_path=trips, foreign_path=stock)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                req = urllib.request.Request(base + "/trip-buy", data=json.dumps(buy(T)).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                self.assertEqual(urllib.request.urlopen(req, timeout=5).status, 204)
                self.assertEqual(load_buys(trips)[0]["qty"], 18)
                got = json.loads(urllib.request.urlopen(base + "/restocks", timeout=5).read())
                self.assertEqual(got["restocks"], [])
            finally:
                server.shutdown()
                server.server_close()


class RestockTest(unittest.TestCase):
    def write(self, d, rows):
        path = Path(d) / "foreign_stock.csv"
        path.write_text("seen,country,item_id,item_name,quantity,cost\n" +
                        "".join(f"{s},{c},{i},{n},{q},{k}\n" for s, c, i, n, q, k in rows), encoding="utf-8")
        return path

    def test_sold_out_then_back_is_a_restock(self):
        with TemporaryDirectory() as d:
            path = self.write(d, [
                (T - 7200, "jap", 206, "Xanax", 50, 800_000),
                (T - 3000, "jap", 206, "Xanax", 0, 800_000),
                (T - 600, "jap", 206, "Xanax", 400, 800_000),
                (T - 300, "jap", 206, "Xanax", 380, 800_000),
                (T - 600, "mex", 261, "Wolverine Plushie", 900, 20),  # first sighting, not a restock
                (T - 9000, "can", 196, "Insulin", 0, 1000),
                (T - 5000, "can", 196, "Insulin", 30, 1000),  # back, but over an hour ago
            ])
            got = recent_restocks(path, T)
        self.assertEqual(got, [{"country": "Japan", "code": "jap", "item_id": 206, "name": "Xanax",
                                "qty": 380, "cost": 800_000, "seen": T - 600}])

    def test_sold_out_again_is_no_longer_news(self):
        with TemporaryDirectory() as d:
            path = self.write(d, [(T - 3000, "jap", 206, "Xanax", 0, 1), (T - 600, "jap", 206, "Xanax", 5, 1),
                                  (T - 60, "jap", 206, "Xanax", 0, 1)])
            self.assertEqual(recent_restocks(path, T), [])


NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const page = JSON.parse(process.argv[3]);
const made = [];
function el(extra) {
  const e = Object.assign({ style: {}, kids: [], _t: "", className: "", attrs: {},
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
let chimes = 0;
global.window = { scrollTo() {}, innerWidth: 0, AudioContext: function () {
  chimes++;
  const node = () => ({ frequency: {}, gain: {}, connect() {}, start() {}, stop() {} });
  return { currentTime: 0, destination: {}, createOscillator: node, createGain: node };
} };
window.top = window;
global.location = { pathname: "/index.php", search: "", hash: "", href: "" };
global.Event = function () {};
Date.now = () => page.now * 1000;
global.document = {
  createElement: () => el(), body: el({ dataset: {} }), documentElement: { scrollHeight: 0 },
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, createTextNode: t => ({ textContent: t, kids: [] }),
};
global.GM_xmlhttpRequest = o => {
  if (/travel-prices$/.test(o.url)) o.onload({ responseText: JSON.stringify({ checked: 1, sell: page.sell }) });
  else if (/restocks$/.test(o.url)) o.onload({ responseText: JSON.stringify({ restocks: page.restocks }) });
  else if (o.onload) o.onload({ responseText: "{}" });
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const text = e => [e.textContent].concat((e.kids || []).map(text)).join("");
const pill = made.find(e => e.id === "th-restock-badge");
const out = { shown: pill.style.display !== "none", chip: text(pill), chimes, store };
if (page.open || page.dismiss) pill.onclick({ target: {} });
out.pill = text(pill);
const link = (function find(e) { if (e.href) return e; for (const k of e.kids || []) { const f = find(k); if (f) return f; } return null; })(pill);
out.href = link ? link.href : null;
if (page.dismiss) {
  pill.kids.find(k => k.textContent === "dismiss").onclick({ stopPropagation() {} });
  out.after = pill.style.display !== "none";
}
process.stdout.write(JSON.stringify(out));
"""

XANAX = {"country": "Japan", "code": "jap", "item_id": 206, "name": "Xanax", "qty": 380, "cost": 800_000, "seen": T - 600}


@unittest.skipUnless(NODE, "node not installed")
class RestockAlertJsTest(unittest.TestCase):
    def run_page(self, **page):
        page = {"now": T, "restocks": [XANAX], "sell": {"206": 830_000}, **page}
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_a_worthwhile_restock_is_a_quiet_chip_that_opens_to_a_fly_link(self):
        out = self.run_page(open=True)
        self.assertTrue(out["shown"])
        self.assertEqual(out["chip"], "🔔 1 back in stock abroad ▸")
        self.assertEqual(out["chimes"], 0)
        self.assertIn("Xanax in Japan: 380 in stock (10 min ago), +$540,000 a bag", out["pill"])
        self.assertEqual(out["href"], "https://www.torn.com/page.php?sid=travel&thfly=Japan")

    def test_the_chime_is_opt_in_and_rings_once_per_restock(self):
        out = self.run_page(store={"tornhoarder.tray.chime": "on"})
        self.assertEqual(out["chimes"], 1)
        self.assertEqual(self.run_page(store=out["store"])["chimes"], 0)

    def test_restock_notices_can_be_switched_off(self):
        self.assertFalse(self.run_page(store={"tornhoarder.tray.restock": "off"})["shown"])

    def test_bag_size_learned_abroad_sets_the_load(self):
        out = self.run_page(open=True, store={"tornhoarder.bagSize": "29"})
        self.assertIn("+$870,000 a bag", out["pill"])

    def test_small_or_unpriced_restocks_stay_quiet(self):
        self.assertFalse(self.run_page(sell={"206": 801_000})["shown"])
        self.assertFalse(self.run_page(sell={})["shown"])

    def test_dismiss_hides_it(self):
        out = self.run_page(dismiss=True)
        self.assertFalse(out["after"])
        self.assertFalse(self.run_page(store=out["store"])["shown"])


if __name__ == "__main__":
    unittest.main()
