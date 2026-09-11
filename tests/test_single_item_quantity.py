"""Regression test: "Single item" mode must report the account's real
owned quantity (minus equipped copies), not a hardcoded 1.

Drives the actual app entry point end-to-end via streamlit.testing.v1.AppTest,
with requests.Session.get patched so no real network calls are made.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"

POLICE_VEST_ID = 33  # data/torn_item_profiles.json: {"id": 33, "name": "Police Vest", "type": "Armor", ...}


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def _fake_get(self, url, headers=None, params=None, timeout=None):
    if url.endswith("/user/inventory"):
        return FakeResponse(
            {
                "inventory": {
                    "items": [
                        {"id": POLICE_VEST_ID, "name": "Police Vest", "amount": 3, "equipped": False, "uid": "111", "faction_owned": False},
                        {"id": POLICE_VEST_ID, "name": "Police Vest", "amount": 1, "equipped": True, "uid": "222", "faction_owned": False},
                    ]
                },
                "_metadata": {"total": 2},
            }
        )
    if f"/market/{POLICE_VEST_ID}/itemmarket" in url:
        return FakeResponse(
            {
                "itemmarket": {
                    "item": {"id": POLICE_VEST_ID, "name": "Police Vest", "type": "Armor", "average_price": 7500},
                    "listings": [
                        {"price": 7000, "amount": 5, "item_details": {"stats": {}}},
                        {"price": 7200, "amount": 3, "item_details": {"stats": {}}},
                    ],
                }
            }
        )
    raise AssertionError(f"unexpected URL in test: {url}")


def _text_input(at, key):
    [match] = [t for t in at.text_input if t.key == key]
    return match


def _radio(at, key):
    [match] = [r for r in at.radio if r.key == key]
    return match


def _form_button(at, label):
    [match] = [b for b in at.button if b.label == label]
    return match


class SingleItemQuantityTest(unittest.TestCase):
    def test_reports_real_owned_quantity_not_hardcoded_one(self):
        at = AppTest.from_file(str(APP))
        at.run(timeout=30)

        _radio(at, "source").set_value("Single item").run(timeout=30)
        _text_input(at, "single_query").set_value("Police Vest").run(timeout=30)
        _text_input(at, "api_key_input").set_value("dummy-key").run(timeout=30)

        with patch("requests.Session.get", _fake_get):
            _form_button(at, "Run").click().run(timeout=30)

        self.assertEqual(list(at.exception), [], msg=[str(e) for e in at.exception])

        df_summary = at.session_state["analysis"]["df_summary"]
        row = df_summary[df_summary["item_id"] == POLICE_VEST_ID].iloc[0]
        # 3 non-equipped + 1 equipped in the fake inventory -> real sellable qty is 3.
        self.assertEqual(int(row["my_quantity"]), 3)


if __name__ == "__main__":
    unittest.main()
