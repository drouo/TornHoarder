"""Re-pricing your active listings (tma.my_listings)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.my_listings import fetch_my_listings, market_listings, review_listing


def mine(price, amount=5, uid=None, quality=None, iid=180, name="Beer"):
    item = {"id": iid, "name": name, "type": "Alcohol", "uid": uid, "stats": {"quality": quality} if uid else None}
    return {"id": 77, "price": price, "amount": amount, "available": amount, "is_anonymous": False, "item": item}


def market(*prices, qualities=None):
    return [{"price": float(p), "amount": 3, "quality": (qualities or [None] * len(prices))[i]} for i, p in enumerate(prices)]


class ReviewListingTest(unittest.TestCase):
    def test_undercut_suggests_a_dollar_under_the_cheapest_other(self):
        r = review_listing(mine(1000), market(950, 1000, 1100))
        self.assertEqual((r["Cheapest other"], r["Cheaper listings"], r["New price"]), (950.0, 1, 949.0))
        self.assertEqual(r["Status"], "undercut by $50")

    def test_your_own_listing_is_left_out(self):
        r = review_listing(mine(1000), market(1000, 1050))
        self.assertEqual((r["Cheapest other"], r["Status"], r["New price"]), (1050.0, "cheapest", None))

    def test_tie_and_wide_gap(self):
        tie = review_listing(mine(1000), market(1000, 1000))
        self.assertEqual((tie["Status"], tie["New price"]), ("tied for cheapest", 999))
        gap = review_listing(mine(500), market(500, 1000))
        self.assertEqual(gap["New price"], 999.0)
        self.assertIn("cheapest by 50%", gap["Status"])

    def test_never_suggests_under_the_shop_or_reference_floor(self):
        # Shop pays 950 -> market floor ceil(950 / 0.95) = 1000.
        r = review_listing(mine(1100), market(990, 1100), shop_price=950)
        self.assertIsNone(r["New price"])
        self.assertIn("under your floor $1,000", r["Status"])

    def test_copies_only_compete_with_equal_or_better_quality(self):
        listing = mine(5000, amount=1, uid=123, quality=40.0, iid=8, name="Axe")
        r = review_listing(listing, market(5000, 3000, 4000, 6000, qualities=[40.0, 20.0, 45.0, 60.0]))
        # The $3,000 copy is worse (q20) and doesn't count; the q45 at $4,000 does.
        self.assertEqual((r["Cheapest other"], r["Cheaper listings"], r["New price"]), (4000.0, 1, 3999.0))

    def test_only_listing(self):
        self.assertEqual(review_listing(mine(1000), market(1000))["Status"], "only listing")

    def test_market_listings_reads_the_wide_row(self):
        row = {"item_id": 1, "price_1": 10, "amount_1": 2, "quality_1": None, "price_2": None, "amount_2": None}
        self.assertEqual(market_listings(row), [{"price": 10.0, "amount": 2, "quality": None}])


class FetchMyListingsTest(unittest.TestCase):
    def test_follows_pages(self):
        class Resp:
            status_code = 200

            def __init__(self, p):
                self.p = p

            def json(self):
                return self.p

        class Sess:
            def __init__(self):
                self.offsets = []

            def get(self, url, headers=None, params=None, timeout=None):
                self.offsets.append(params["offset"])
                if params["offset"] == 0:
                    return Resp({"itemmarket": [mine(1), mine(2)], "_metadata": {"links": {"next": "x"}}})
                return Resp({"itemmarket": [mine(3)], "_metadata": {"links": {"next": None}}})

        s = Sess()
        out, err = fetch_my_listings(s, "k")
        self.assertEqual(([l["price"] for l in out], s.offsets, err), ([1, 2, 3], [0, 2], None))


class MyListingsAppTest(unittest.TestCase):
    def test_run_reviews_a_listing_for_an_item_you_no_longer_hold(self):
        from unittest.mock import patch

        from streamlit.testing.v1 import AppTest

        from test_price_history import APP, _fake_get

        listing = mine(520, iid=187, name="Teddy Bear Plushie")
        at = AppTest.from_file(str(APP))
        at.run(timeout=30)
        [sell] = [m for m in at.multiselect if m.key == "sell_categories"]
        sell.set_value(["Plushie"]).run(timeout=30)
        [key] = [t for t in at.text_input if t.key == "api_key_input"]
        key.set_value("dummy-key").run(timeout=30)
        with patch("requests.Session.get", _fake_get), patch("tma.my_listings.fetch_my_listings", lambda *a, **k: ([listing], None)):
            [run] = [b for b in at.button if b.label == "Run"]
            run.click().run(timeout=60)
        self.assertEqual(list(at.exception), [], [str(e) for e in at.exception])
        (row,) = at.session_state["analysis"]["my_listings"]
        # The fake market lists 480 and 500; 480 undercuts the 520 listing.
        self.assertEqual((row["Item"], row["Cheapest other"], row["New price"]), ("Teddy Bear Plushie", 480.0, 479.0))


if __name__ == "__main__":
    unittest.main()
