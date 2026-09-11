"""Regression test: if every item-market lookup fails, the app must show
a clear error instead of crashing.

Previously, per_id_rows could be entirely made of error placeholders
(dicts with no "item_name"/price_* keys). Those still got built into
df_market, which then had no "item_name" column at all, and
wide_to_long() raised an unhandled KeyError deep inside the pipeline
instead of the run failing gracefully.
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
        return FakeResponse({"inventory": {"items": []}, "_metadata": {"total": 0}})
    if f"/market/{POLICE_VEST_ID}/itemmarket" in url:
        # Non-transient API error -> fetch_item_market returns an error
        # placeholder row with no item_name/price_* keys at all.
        return FakeResponse({"error": {"code": 2, "error": "Incorrect key"}})
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


class MarketPhaseAllFailedTest(unittest.TestCase):
    def test_shows_error_instead_of_crashing(self):
        at = AppTest.from_file(str(APP))
        at.run(timeout=30)

        _radio(at, "source").set_value("Single item").run(timeout=30)
        _text_input(at, "single_query").set_value("Police Vest").run(timeout=30)
        _text_input(at, "api_key_input").set_value("dummy-key").run(timeout=30)

        with patch("requests.Session.get", _fake_get):
            _form_button(at, "Run").click().run(timeout=30)

        self.assertEqual(list(at.exception), [], msg=[str(e) for e in at.exception])
        self.assertNotIn("has_data", at.session_state)
        self.assertTrue(any("no market data was retrieved" in e.value.lower() for e in at.error))


if __name__ == "__main__":
    unittest.main()
