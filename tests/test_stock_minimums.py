"""Keep stocked: what's short, where to buy it, the status file the in-Torn
badge reads, and the badge / Item Market restock mode in Node."""

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
from tma.config import load_minimums, save_minimums
from tma.stock_minimums import as_reserves, check_stock, load_status, restock_route, write_status
from tma.userscript import build_userscript

PROFILES = {
    "67": {"name": "First Aid Kit", "type": "Medical", "buy_price": 15000, "market_price": 10812, "vendor": "Pharmacy"},
    "68": {"name": "Small First Aid Kit", "type": "Medical", "buy_price": 4000, "market_price": 4625, "vendor": "Pharmacy"},
    "1363": {"name": "Ipecac Syrup", "type": "Medical", "buy_price": None, "market_price": 44147, "vendor": None},
}
EQUIPMENT = [
    {"slot": 1, "name": "Jackhammer", "ammo": {"id": 1, "name": "12 Gauge Cartridge", "type": "Standard", "quantity": 144}},
    {"slot": 3, "name": "Kitchen Knife"},
]
MINIMUMS = {"item:67": 50, "item:68": 50, "item:1363": 10, "ammo:primary": 1000, "ammo:secondary": 500}


class CheckStockTest(unittest.TestCase):
    def rows(self):
        return {r["key"]: r for r in check_stock(MINIMUMS, {67: 38, 68: 60, 1363: 14}, EQUIPMENT, PROFILES, now=1700)}

    def test_shortfalls_and_cheapest_source(self):
        rows = self.rows()
        fak = rows["item:67"]
        self.assertEqual((fak["have"], fak["need"], fak["source"]), (38, 12, "Item Market"))  # $10.8k there vs $15k shop
        # cap = 10% over market value; 1700 = when stock was checked
        self.assertEqual(fak["url"], "https://www.torn.com/page.php?sid=ItemMarket&thneed=67:12:11893:1700"
                                     "#/market/view=search&itemID=67")
        self.assertEqual((rows["item:68"]["need"], rows["item:68"]["url"]), (0, None))
        self.assertEqual(rows["item:68"]["source"], "Pharmacy")  # shop is cheaper for this one
        self.assertEqual(rows["item:1363"]["need"], 0)

    def test_ammo_follows_the_equipped_gun(self):
        rows = self.rows()
        ammo = rows["ammo:primary"]
        self.assertEqual(ammo["name"], "12 Gauge Cartridge (Primary: Jackhammer)")
        self.assertEqual((ammo["have"], ammo["need"]), (144, 856))
        self.assertEqual(ammo["url"], "https://www.torn.com/ammo.php?thammo=856&thammoname=12%20Gauge%20Cartridge")
        self.assertNotIn("ammo:secondary", rows)  # no gun in slot 2

    def test_minimums_are_reserves_and_status_lists_only_what_is_short(self):
        self.assertEqual(as_reserves(MINIMUMS), {67: 50, 68: 50, 1363: 10})
        with TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            write_status(path, list(self.rows().values()))
            status = load_status(path)
            self.assertEqual(status["checked"], 1700)
            self.assertEqual([s["name"] for s in status["short"]], ["12 Gauge Cartridge (Primary: Jackhammer)", "First Aid Kit"])
            self.assertEqual(load_status(Path(d) / "missing.json"), {"checked": 0, "short": []})

    def test_restock_route_groups_each_shop_then_market_then_ammo(self):
        profiles = dict(PROFILES, **{
            "226": {"name": "Smoke Grenade", "buy_price": 20000, "market_price": 74779, "vendor": "Big Al's Gun Shop"},
            "392": {"name": "Pepper Spray", "buy_price": 800, "market_price": 2209, "vendor": "Big Al's Gun Shop"},
        })
        mins = dict(MINIMUMS, **{"item:226": 50, "item:392": 50})
        rows = check_stock(mins, {67: 38, 68: 60, 1363: 4, 392: 1}, EQUIPMENT, profiles, now=1700)
        route = restock_route(rows)
        self.assertEqual([r["label"] for r in route], [
            "Big Al's Gun Shop: 50 Smoke Grenade, 49 Pepper Spray",
            "Item Market: 6 Ipecac Syrup",
            "Item Market: 12 First Aid Kit",
            "Ammo: 856 12 Gauge Cartridge",
        ])
        self.assertEqual(route[0]["url"], "https://www.torn.com/bigalgunshop.php?thbuy=226:50,392:49&thchk=1700")
        with TemporaryDirectory() as d:
            self.assertEqual(write_status(Path(d) / "s.json", rows, now=1)["route"], route)

    def test_config_round_trip_drops_zeros(self):
        save_minimums({"item:67": 50, "item:68": 0})
        self.assertEqual(load_minimums(), {"item:67": 50})


class StockStatusServerTest(unittest.TestCase):
    def test_serves_the_latest_status(self):
        with TemporaryDirectory() as d:
            status = Path(d) / "s.json"
            status.write_text('{"checked": 5, "short": []}', encoding="utf-8")
            server = build_server(Path(d) / "x.user.js", port=0, status_path=status)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/stock-status"
                self.assertEqual(json.loads(urllib.request.urlopen(url, timeout=5).read()), {"checked": 5, "short": []})
            finally:
                server.shutdown()
                server.server_close()


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
    click() {} }, extra || {});
  made.push(e);
  return e;
}
const store = page.store || {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } };
global.window = { scrollTo() {}, innerWidth: 0 };
window.top = window;
global.location = Object.assign({ pathname: "/", search: "", hash: "", href: "" }, page.location);
global.Event = function () {};
global.HTMLInputElement = { prototype: {} };
Object.defineProperty(HTMLInputElement.prototype, "value", { set(v) { this._v = v; }, configurable: true });
const sellers = (page.sellers || []).map(s => {
  const input = el({ attrs: { "aria-label": s.name + " amount" } });
  return el({ querySelector(sel) {
    if (/hidden\]$/.test(sel)) return null;
    if (/input/.test(sel)) return input;
    if (/price/.test(sel)) return el({ textContent: s.price });
    if (/available/.test(sel)) return el({ textContent: s.avail });
    return null;
  } });
});
global.document = {
  createElement: () => el(), body: el({ dataset: {} }), documentElement: { scrollHeight: 0 },
  getElementById: () => null, querySelector: () => null,
  querySelectorAll: s => (/sellerList/.test(s) ? sellers : []),
  createTextNode: t => ({ textContent: t, kids: [] }),
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.console = { log() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const text = e => [e.textContent].concat((e.kids || []).map(text)).join("\n");
const badge = made.find(e => e.id === "th-stock-badge");
const out = { badgeShown: badge.style.display, badge: text(badge) };
if (page.openBadge) { badge.onclick({ target: {} }); out.badgeOpen = text(badge); }
const btn = made.find(e => e.id === "th-restock-btn");
if (btn) out.button = btn.textContent;
if (btn && page.click) { btn.onclick(); out.went = location.href; out.run = store["tornhoarder.restockRun"] || null; }
out.store = store;
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node not installed")
class StockJsTest(unittest.TestCase):
    def run_page(self, page, stock=None):
        with TemporaryDirectory() as d:
            d = Path(d)
            (d / "s.user.js").write_text(build_userscript({}, {}, stock=stock), encoding="utf-8")
            (d / "h.js").write_text(HARNESS, encoding="utf-8")
            res = subprocess.run([NODE, str(d / "h.js"), str(d / "s.user.js"), json.dumps(page)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            return json.loads(res.stdout)

    def test_badge_hidden_when_nothing_is_short(self):
        self.assertEqual(self.run_page({})["badgeShown"], "none")

    def test_badge_lists_shortfalls_with_buy_links(self):
        stock = {"checked": 0, "short": [{"name": "First Aid Kit", "have": 38, "min": 50, "need": 12, "url": "u"}]}
        out = self.run_page({"openBadge": True}, stock=stock)
        self.assertEqual(out["badgeShown"], "block")
        self.assertIn("⚠ 1 item(s) below minimum ▸", out["badge"])
        self.assertIn("First Aid Kit: 38 of 50", out["badgeOpen"])
        self.assertIn("buy 12 →", out["badgeOpen"])

    def test_market_restock_picks_the_cheapest_listing_under_the_cap(self):
        page = {"location": {"pathname": "/page.php", "search": "?sid=ItemMarket&thneed=67:12:11893"},
                "sellers": [{"name": "First Aid Kit", "price": "$12,500", "avail": "40 available"},
                            {"name": "First Aid Kit", "price": "$10,000", "avail": "5 available"}]}
        self.assertEqual(self.run_page(page)["button"], "Buy 5 × First Aid Kit @ $10,000 (cheapest)")
        page["sellers"] = [{"name": "First Aid Kit", "price": "$12,500", "avail": "40 available"}]
        self.assertEqual(self.run_page(page)["button"], "Cheapest is $12,500, over your $11,893 cap")


@unittest.skipUnless(NODE, "node not installed")
class NoBuyLoopJsTest(StockJsTest):
    PAGE = {"location": {"pathname": "/page.php", "search": "?sid=ItemMarket&thneed=67:12:11893:1700"},
            "sellers": [{"name": "First Aid Kit", "price": "$10,000", "avail": "40 available"}]}

    def test_a_fill_counts_at_once_even_if_torn_confirms_it(self):
        # Before: only a confirm recognised by the script counted, so clicking
        # Torn's own confirm left the count at 0 and every click bought again.
        out = self.run_page(dict(self.PAGE, click=True, store={}))
        self.assertEqual(out["button"], "Buy 12 × First Aid Kit @ $10,000 (cheapest)")
        log = json.loads(out["store"]["tornhoarder.bought"])
        self.assertEqual([q for _, q in log["67"]], [12])
        # Reloaded (or reopened from the badge): nothing more to buy.
        again = self.run_page(dict(self.PAGE, store=out["store"]))
        self.assertEqual(again["button"], "Restocked ✓ (click to close)")

    def test_purchases_before_the_check_dont_count(self):
        old = {"tornhoarder.bought": json.dumps({"67": [[1_000_000, 12]]})}  # 1970: before the 1700s check
        self.assertEqual(self.run_page(dict(self.PAGE, store=old))["button"], "Buy 12 × First Aid Kit @ $10,000 (cheapest)")


@unittest.skipUnless(NODE, "node not installed")
class RestockRunJsTest(StockJsTest):
    def test_a_finished_stop_goes_on_to_the_next(self):
        run = {"stops": [{"label": "Item Market: 12 First Aid Kit", "url": "a"},
                         {"label": "Ammo: 856 12 Gauge Cartridge", "url": "b"}], "i": 0, "started": 4102444800000}
        page = {"location": {"pathname": "/page.php", "search": "?sid=ItemMarket&thneed=67:0:11893"},
                "store": {"tornhoarder.restockRun": json.dumps(run)}, "click": True}
        out = self.run_page(page)
        self.assertEqual(out["button"], "Restocked ✓ · Next: Ammo: 856 12 Gauge Cartridge →")
        self.assertEqual(out["went"], "b")
        self.assertEqual(json.loads(out["run"])["i"], 1)

    def test_last_stop_ends_the_run(self):
        run = {"stops": [{"label": "Item Market: 12 First Aid Kit", "url": "a"}], "i": 0, "started": 4102444800000}
        page = {"location": {"pathname": "/page.php", "search": "?sid=ItemMarket&thneed=67:0:11893"},
                "store": {"tornhoarder.restockRun": json.dumps(run)}, "click": True}
        out = self.run_page(page)
        self.assertEqual(out["button"], "Restocked ✓ · Restock run done ✓ (click to close)")
        self.assertIsNone(out["run"])


if __name__ == "__main__":
    unittest.main()
