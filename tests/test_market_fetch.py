import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.market_fetch import fetch_all_item_markets, fetch_item_market


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload_by_url):
        self._payload_by_url = payload_by_url

    def get(self, url, headers=None, params=None, timeout=None):
        return FakeResponse(self._payload_by_url[url])


def _listing(price, amount):
    return {"price": price, "amount": amount, "item_details": {"stats": {}}}


class FetchItemMarketTest(unittest.TestCase):
    def test_flattens_listings_into_wide_row(self):
        url = "https://api.torn.com/v2/market/26/itemmarket"
        payload = {
            "itemmarket": {
                "item": {"id": 26, "name": "AK-47", "type": "Primary", "average_price": 1000},
                "listings": [_listing(900, 1), _listing(950, 2)],
            }
        }
        session = FakeSession({url: payload})
        row = fetch_item_market(session, bucket=None, api_key="k", item_id=26, my_quantity=3)
        self.assertEqual(row["item_name"], "AK-47")
        self.assertEqual(row["price_1"], 900)
        self.assertEqual(row["amount_2"], 2)
        self.assertIsNone(row["price_3"])
        self.assertEqual(row["my_quantity"], 3)

    def test_api_error_returns_error_row(self):
        url = "https://api.torn.com/v2/market/26/itemmarket"
        session = FakeSession({url: {"error": {"code": 2, "error": "Incorrect key"}}})
        row = fetch_item_market(session, bucket=None, api_key="bad", item_id=26, my_quantity=1)
        self.assertIn("error", row)
        self.assertEqual(row["item_id"], 26)


class FetchAllItemMarketsTest(unittest.TestCase):
    def test_fetches_every_item_concurrently(self):
        calls = []

        def fake_fetch(session, bucket, api_key, item_id, qty):
            calls.append(item_id)
            return {"item_id": item_id, "my_quantity": qty}

        with patch("tma.market_fetch.fetch_item_market", side_effect=fake_fetch):
            rows, truncated = fetch_all_item_markets(
                session=None,
                bucket=None,
                api_key="k",
                item_ids=[1, 2, 3],
                qty_by_id={1: 5, 2: 6, 3: 7},
                max_workers=3,
            )
        self.assertFalse(truncated)
        self.assertEqual(set(calls), {1, 2, 3})
        self.assertEqual(rows[2]["my_quantity"], 6)

    def test_progress_callback_invoked_per_item(self):
        progress_calls = []

        def fake_fetch(session, bucket, api_key, item_id, qty):
            return {"item_id": item_id}

        with patch("tma.market_fetch.fetch_item_market", side_effect=fake_fetch):
            fetch_all_item_markets(
                session=None,
                bucket=None,
                api_key="k",
                item_ids=[1, 2],
                qty_by_id={1: 1, 2: 1},
                max_workers=2,
                on_progress=lambda done, total, row: progress_calls.append((done, total)),
            )
        self.assertEqual(len(progress_calls), 2)
        self.assertEqual(progress_calls[-1], (2, 2))


if __name__ == "__main__":
    unittest.main()
