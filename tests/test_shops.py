"""Tests for the city-shop route (src/tma/shops.py) and its userscript embedding."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import threading
import urllib.error
import urllib.request
from tempfile import TemporaryDirectory

from tma.autoupdate_server import build_server
from tma.shops import build_shop_run, load_shop_knowledge, record_shop_report, shop_price_for, shop_step_for
from tma.userscript import build_userscript

PROFILES = {
    "1483": {"name": "Insulin", "vendor": "Pharmacy", "sell_price": 9000},
    "26": {"name": "AK-47", "vendor": "Big Al's Gun Shop", "sell_price": 10000},
    "1485": {"name": "Shark Fin", "vendor": "Nikeh Sports", "sell_price": 66000},
}


class ShopPriceTest(unittest.TestCase):
    def test_reachable_shop_price(self):
        self.assertEqual(shop_price_for(PROFILES["1483"]), 9000.0)

    def test_unreachable_shop_has_no_price(self):
        # Nikeh Sports: "This area is not yet available to you".
        self.assertIsNone(shop_price_for(PROFILES["1485"]))
        self.assertIsNone(shop_price_for({"vendor": None, "sell_price": 50}))


class BuildShopRunTest(unittest.TestCase):
    def test_route_ordered_by_payout_with_copies_by_uid(self):
        rows = [
            {"item_id": 1483, "qty": 431, "uid": "21323992478", "is_stat": False, "price": 9000},
            {"item_id": 26, "qty": 1, "uid": "111", "is_stat": True, "price": 10000},
            {"item_id": 26, "qty": 1, "uid": "222", "is_stat": True, "price": 10000},
        ]
        run = build_shop_run(rows, PROFILES)
        self.assertEqual([s["step"] for s in run["route"]], ["pharmacy", "bigalgunshop"])
        self.assertEqual(run["route"][0]["payout"], 431 * 9000)
        self.assertEqual(run["route"][0]["url"], "https://www.torn.com/shops.php?step=pharmacy")
        # Big Al's isn't under shops.php; its Sell page is bigalgunshop.php.
        self.assertEqual(run["route"][1]["url"], "https://www.torn.com/bigalgunshop.php")
        # Stackables are allowed by item; weapon copies only by their uid.
        self.assertEqual(run["items"]["1483"], {"qty": 431, "uids": None, "price": 9000, "step": "pharmacy", "name": "Insulin"})
        self.assertEqual(run["items"]["26"]["uids"], ["111", "222"])

    def test_copy_without_uid_and_unreachable_shop_are_skipped(self):
        rows = [
            {"item_id": 26, "qty": 1, "uid": None, "is_stat": True, "price": 10000},
            {"item_id": 1485, "qty": 3, "uid": None, "is_stat": False, "price": 66000},
        ]
        self.assertEqual(build_shop_run(rows, PROFILES), {"route": [], "items": {}})


class ShopKnowledgeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "shop_buys.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_refused_item_has_no_shop_and_seen_item_routes_to_its_buyer(self):
        # Big Al's listed AK-47s but not the Leather Gloves we routed there.
        record_shop_report(self.path, {"step": "bigalgunshop", "name": "Big Al's Gun Shop", "buys": [26, 8], "rejects": [650]})
        # The Pharmacy listed Hydrochloric Acid although its vendor is the General Store.
        record_shop_report(self.path, {"step": "pharmacy", "buys": [1483, 1477], "rejects": []})
        known = load_shop_knowledge(self.path)
        gloves = {"vendor": "Big Al's Gun Shop", "sell_price": 600}
        self.assertIsNone(shop_step_for(650, gloves, known))
        self.assertIsNone(shop_price_for(gloves, 650, known))
        self.assertEqual(shop_step_for(26, PROFILES["26"], known), "bigalgunshop")
        self.assertEqual(shop_step_for(1477, {"vendor": "General Store", "sell_price": 55}, known), "pharmacy")
        # Never-visited shops still fall back to the catalog vendor.
        self.assertEqual(shop_step_for(999, {"vendor": "Docks", "sell_price": 10}, known), "docks")

    def test_once_seen_buying_an_item_stays_a_buy(self):
        # First visit lists Insulin; a later visit after selling it doesn't.
        record_shop_report(self.path, {"step": "pharmacy", "buys": [1483], "rejects": []})
        record_shop_report(self.path, {"step": "pharmacy", "buys": [1491], "rejects": [1483]})
        k = load_shop_knowledge(self.path)["pharmacy"]
        self.assertEqual((k["buys"], k["rejects"]), ([1483, 1491], []))

    def test_route_skips_refused_items(self):
        record_shop_report(self.path, {"step": "bigalgunshop", "buys": [8], "rejects": [26]})
        rows = [{"item_id": 26, "qty": 1, "uid": "111", "is_stat": True, "price": 10000}]
        self.assertEqual(build_shop_run(rows, PROFILES, load_shop_knowledge(self.path))["route"], [])

    def test_bad_reports_are_rejected(self):
        for bad in ({"step": "../etc", "buys": []}, {"step": "pharmacy", "buys": ["x"]}):
            with self.assertRaises(ValueError):
                record_shop_report(self.path, bad)
        self.assertFalse(self.path.exists())


class ShopReportServerTest(unittest.TestCase):
    def test_post_records_report_and_get_still_serves_script(self):
        with TemporaryDirectory() as d:
            script, reports = Path(d) / "s.user.js", Path(d) / "shop_buys.json"
            script.write_text("// script", encoding="utf-8")
            server = build_server(script, port=0, reports_path=reports)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                body = json.dumps({"step": "jewelry", "buys": [1486], "rejects": []}).encode()
                req = urllib.request.Request(f"http://127.0.0.1:{port}/shop-report", data=body,
                                             headers={"Content-Type": "application/json"}, method="POST")
                self.assertEqual(urllib.request.urlopen(req, timeout=5).status, 204)
                self.assertEqual(load_shop_knowledge(reports)["jewelry"]["buys"], [1486])
                bad = urllib.request.Request(f"http://127.0.0.1:{port}/shop-report", data=b"{nope",
                                             headers={"Content-Type": "application/json"}, method="POST")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(bad, timeout=5)
                self.assertEqual(ctx.exception.code, 400)
                got = urllib.request.urlopen(f"http://127.0.0.1:{port}/s.user.js", timeout=5).read()
                self.assertEqual(got, b"// script")
            finally:
                server.shutdown()
                server.server_close()


class UserscriptShopTest(unittest.TestCase):
    def test_embeds_shop_run_and_matches_shop_pages(self):
        run = build_shop_run([{"item_id": 1483, "qty": 2, "uid": None, "is_stat": False, "price": 9000}], PROFILES)
        out = build_userscript({}, {}, shop_run=run)
        self.assertIn("// @match        https://www.torn.com/shops.php*", out)
        self.assertIn("// @match        https://www.torn.com/bigalgunshop.php*", out)
        self.assertIn("// @grant        GM_xmlhttpRequest", out)
        self.assertIn('"reportUrl": "http://127.0.0.1:9999/shop-report"', out)
        start = out.index("var SHOP = ") + len("var SHOP = ")
        self.assertEqual(json.loads(out[start:out.index(";", start)]), run)

    def test_no_shop_run_embeds_an_empty_one(self):
        out = build_userscript({}, {})
        self.assertIn('var SHOP = {"route": [], "items": {}};', out)


if __name__ == "__main__":
    unittest.main()
