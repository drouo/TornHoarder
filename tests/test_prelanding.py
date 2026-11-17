"""Fresh prices before landing abroad (tma.prelanding) and the /flying route."""

import json
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.autoupdate_server import build_server
from tma.prelanding import LEAD_S, PreLanding, check_destination, country_code, merge_prices, parse_travel
from tma.travel_plan import History

NOW = 1_800_000_000
# The shape of Torn v2 user/travel.
FLIGHT = {"travel": {"destination": "Canada", "method": "Airstrip", "departed_at": NOW - 600,
                     "arrival_at": NOW + 1200, "time_left": 1200}}


def canada() -> History:
    h = History()
    h.cost = {("can", 1483): 2620, ("can", 1484): 28_620, ("can", 262): 400, ("mex", 260): 300}
    h.names = {1483: "Insulin", 1484: "Bear Gall", 262: "Hockey Stick", 260: "Dahlia"}
    return h


class FakeTimer:
    started = []

    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args, self.daemon = delay, fn, args, False

    def start(self):
        FakeTimer.started.append(self)


class PreLandingTest(unittest.TestCase):
    def test_reads_where_to_and_when_you_land(self):
        self.assertEqual(parse_travel(FLIGHT), ("can", NOW + 1200))
        home = {"travel": dict(FLIGHT["travel"], destination="Torn")}
        landed = {"travel": dict(FLIGHT["travel"], time_left=0)}
        for data in (home, landed, None, {"error": {"code": 2}}):
            self.assertIsNone(parse_travel(data))
        self.assertEqual(country_code("United Arab Emirates"), "uae")
        self.assertEqual(country_code("UAE"), "uae")

    def test_one_check_per_flight_a_few_minutes_before_landing(self):
        FakeTimer.started = []
        logs, lookups = [], []
        clock = [NOW]
        pre = PreLanding(lambda: lookups.append(1) or (FLIGHT, None), lambda code: "checked " + code, logs.append,
                         clock=lambda: clock[0], timer=FakeTimer)
        pre.flying()
        self.assertEqual(len(FakeTimer.started), 1)
        t = FakeTimer.started[0]
        self.assertEqual((t.delay, t.args, t.daemon), (1200 - LEAD_S, ("can",), True))
        self.assertIn("flying to Canada, landing in 20 min; prices checked in 17 min", logs[0])
        clock[0] += 300  # more Torn pages in the air: no new lookup, no second check
        pre.flying()
        self.assertEqual((len(lookups), len(FakeTimer.started)), (1, 1))
        t.fn(*t.args)
        self.assertEqual(logs[-1], "pre-landing: checked can")

    def test_flights_home_and_failures_are_logged_not_scheduled(self):
        FakeTimer.started = []
        logs = []
        home = {"travel": dict(FLIGHT["travel"], destination="Torn")}
        PreLanding(lambda: (home, None), lambda c: "", logs.append, clock=lambda: NOW, timer=FakeTimer).flying()
        self.assertEqual(FakeTimer.started, [])
        self.assertIn("no flight abroad to check", logs[0])

        def boom(code):
            raise RuntimeError("API down")

        pre = PreLanding(lambda: (FLIGHT, None), boom, logs.append, clock=lambda: NOW, timer=FakeTimer)
        pre.flying()
        FakeTimer.started[-1].fn("can")
        self.assertEqual(logs[-1], "pre-landing: check for Canada failed: RuntimeError: API down")

    def test_checks_the_destinations_items_and_keeps_the_rest(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "travel_prices.json"
            path.write_text(json.dumps({"checked": 1, "sell": {"260": 2423, "1483": 1}, "via": {"1483": "Old"}}))
            refs = {1483: 9000, 1484: 35_000, 262: 4600, 260: 2600}
            offers = {1483: {"price": 8794.0, "trader": "Shekels"}, 262: {"price": 4295.0, "trader": "Shekels"}}
            asked = []

            def fetch(ids):
                asked.append(sorted(ids))
                return {1483: {"price_1": 9100, "amount_1": 5}, 1484: {"price_1": 35_000, "amount_1": 2},
                        262: {"price_1": 4700, "amount_1": 9}}

            line = check_destination("can", canada(), refs, offers, fetch, path, now=NOW)
            saved = json.loads(path.read_text())
        self.assertEqual(asked, [[262, 1483, 1484]])
        self.assertEqual(saved["checked"], NOW)
        self.assertEqual(saved["sell"], {"260": 2423, "1483": 8794, "1484": 33249, "262": 4370})
        self.assertEqual(saved["via"], {"1483": "Shekels"})
        self.assertEqual(line, "checked 3 of 3 items in Canada; best: Insulin +$6,174 to Shekels, "
                               "Bear Gall +$4,629, Hockey Stick +$3,970")

    def test_merge_without_a_file_yet(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            merge_prices(path, {1: 10.4}, {}, [1], NOW)
            self.assertEqual(json.loads(path.read_text()), {"checked": NOW, "sell": {"1": 10}})

    def test_the_userscript_reports_a_flight_to_the_local_server(self):
        called = threading.Event()
        with TemporaryDirectory() as d:
            server = build_server(Path(d) / "x.user.js", port=0, on_flying=called.set)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/flying", data=b"{}",
                                             headers={"Content-Type": "application/json"}, method="POST")
                self.assertEqual(urllib.request.urlopen(req, timeout=5).status, 204)
                self.assertTrue(called.wait(5))
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
