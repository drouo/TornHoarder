"""Re-pricing your active listings (tma.my_listings)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.inventory import InventoryEntry
from tma.my_listings import drop_listed_copies, fetch_my_listings, market_listings, review_listing, without_mine


def mine(price, amount=5, uid=None, quality=None, iid=180, name="Beer"):
    item = {"id": iid, "name": name, "type": "Alcohol", "uid": uid, "stats": {"quality": quality} if uid else None}
    return {"id": 77, "price": price, "amount": amount, "available": amount, "is_anonymous": False, "item": item}


def market(*prices, qualities=None):
    return [{"price": float(p), "amount": 3, "quality": (qualities or [None] * len(prices))[i]} for i, p in enumerate(prices)]


class ReviewListingTest(unittest.TestCase):
    def test_undercut_suggests_a_dollar_under_the_cheapest_other(self):
        r = review_listing(mine(1000), market(950, 1100))
        self.assertEqual((r["Cheapest other"], r["Cheaper listings"], r["New price"]), (950.0, 1, 949.0))
        self.assertEqual(r["Status"], "undercut by $50")

    def test_tie_and_wide_gap(self):
        tie = review_listing(mine(1000), market(1000))
        self.assertEqual((tie["Status"], tie["New price"]), ("tied for cheapest", 999))
        gap = review_listing(mine(500), market(1000))
        self.assertEqual(gap["New price"], 999.0)
        self.assertIn("cheapest by 50%", gap["Status"])

    def test_never_suggests_under_the_shop_or_reference_floor(self):
        # Shop pays 950 -> market floor ceil(950 / 0.95) = 1000.
        r = review_listing(mine(1100), market(990), shop_price=950)
        self.assertIsNone(r["New price"])
        self.assertIn("under your floor $1k", r["Status"])

    def test_copies_only_compete_with_equal_or_better_quality(self):
        listing = mine(5000, amount=1, uid=123, quality=40.0, iid=8, name="Axe")
        r = review_listing(listing, market(3000, 4000, 6000, qualities=[20.0, 45.0, 60.0]))
        # The $3,000 copy is worse (q20) and doesn't count; the q45 at $4,000 does.
        self.assertEqual((r["Cheapest other"], r["Cheaper listings"], r["New price"]), (4000.0, 1, 3999.0))

    def test_only_listing(self):
        self.assertEqual(review_listing(mine(1000), [])["Status"], "only listing")

    def test_market_listings_reads_the_wide_row(self):
        row = {"item_id": 1, "price_1": 10, "amount_1": 2, "quality_1": None, "price_2": None, "amount_2": None}
        self.assertEqual(market_listings(row), [{"price": 10.0, "amount": 2, "quality": None}])


def wide(*listings, iid=110):
    row = {"item_id": iid, "item_name": "Leather Bullwhip"}
    for i, (price, amount, quality, uid) in enumerate(listings, start=1):
        row.update({f"price_{i}": price, f"amount_{i}": amount, f"quality_{i}": quality, f"uid_{i}": uid})
    return row


class WithoutMineTest(unittest.TestCase):
    # Live: 20 Leather Bullwhips of yours, $1,351 to $1,900, each priced and
    # reviewed against the others, so you undercut yourself copy by copy.
    def test_your_copies_are_not_the_market(self):
        row = wide((1351, 1, 27.8, 900), (1360, 1, 30.0, 555), (1900, 1, 5.2, 901), (2000, 1, 40.0, 556))
        listed = [mine(1351, 1, uid=900, quality=27.8, iid=110), mine(1900, 1, uid=901, quality=5.2, iid=110)]
        out = without_mine(row, listed)
        self.assertEqual(market_listings(out), [{"price": 1360.0, "amount": 1, "quality": 30.0},
                                                {"price": 2000.0, "amount": 1, "quality": 40.0}])
        self.assertEqual((out["price_3"], out["uid_4"]), (None, None))
        r = review_listing(listed[1], market_listings(out))
        self.assertEqual(r["New price"], 1359.0)  # under the other seller, never under your $1,351

    def test_stacks_match_by_price_and_amount_once_each(self):
        row = wide((10, 5, None, None), (10, 5, None, None), (10, 3, None, None), iid=180)
        out = without_mine(row, [mine(10, amount=5)])
        self.assertEqual([(l["price"], l["amount"]) for l in market_listings(out)], [(10.0, 5), (10.0, 3)])

    def test_other_items_and_no_listings_are_untouched(self):
        row = wide((10, 5, None, None), iid=180)
        self.assertIs(without_mine(row, []), row)
        self.assertIs(without_mine(row, [mine(10, amount=5, iid=181)]), row)


class DropListedCopiesTest(unittest.TestCase):
    def test_a_listed_copy_is_left_out_even_if_inventory_still_shows_it(self):
        inv = [
            InventoryEntry(item_id=240, name="Type 98 Anti Tank", amount=1, equipped=False, uid="17885770351"),
            InventoryEntry(item_id=240, name="Type 98 Anti Tank", amount=1, equipped=False, uid="111"),
            InventoryEntry(item_id=180, name="Beer", amount=5, equipped=False, uid=None),
        ]
        listed = [mine(21087329, amount=1, uid=17885770351, quality=8.76, iid=240, name="Type 98 Anti Tank")]
        kept = drop_listed_copies(inv, listed)
        self.assertEqual([(e.item_id, e.uid) for e in kept], [(240, "111"), (180, None)])


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
