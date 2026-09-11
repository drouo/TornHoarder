"""Regression tests for "Single item" mode's real-quantity lookup:

1. It must report the account's real owned quantity (minus equipped
   copies), not a hardcoded 1.
2. Each owned copy is its own distinct item with its own stat roll (Torn
   never stacks weapon/armor items — each is amount=1 with a unique uid),
   so it must fetch and keep per-copy stats rather than pricing every
   copy off the market median. An earlier version of this fix got #1
   right but missed #2: it fetched the real entries but never read each
   uid's actual stats, so distinct copies all still priced identically.

Drives the actual app entry point end-to-end via streamlit.testing.v1.AppTest,
with requests.Session.get patched so no real network calls are made.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"

POLICE_VEST_ID = 33  # data/torn_item_profiles.json: {"id": 33, "name": "Police Vest", "type": "Armor", ...}

# Three distinct owned copies (Torn never stacks armor/weapons — each is its
# own amount=1 entry with a unique uid and its own stat roll) plus one
# equipped copy that should be excluded from both quantity and pricing.
OWNED_COPIES = {"111": 30.0, "222": 34.5, "333": 28.0}
EQUIPPED_UID = "999"


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def _fake_get(self, url, headers=None, params=None, timeout=None):
    if url.endswith("/user/inventory"):
        items = [
            {"id": POLICE_VEST_ID, "name": "Police Vest", "amount": 1, "equipped": False, "uid": uid, "faction_owned": False}
            for uid in OWNED_COPIES
        ]
        items.append(
            {"id": POLICE_VEST_ID, "name": "Police Vest", "amount": 1, "equipped": True, "uid": EQUIPPED_UID, "faction_owned": False}
        )
        return FakeResponse({"inventory": {"items": items}, "_metadata": {"total": len(items)}})

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

    for uid, armor in OWNED_COPIES.items():
        if url.endswith(f"/torn/{uid}"):
            return FakeResponse({"itemdetails": {"uid": uid, "stats": {"damage": None, "accuracy": None, "armor": armor, "quality": None}}})

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


def _run_single_item_lookup(query: str):
    at = AppTest.from_file(str(APP))
    at.run(timeout=30)

    _radio(at, "source").set_value("Single item").run(timeout=30)
    _text_input(at, "single_query").set_value(query).run(timeout=30)
    _text_input(at, "api_key_input").set_value("dummy-key").run(timeout=30)

    with patch("requests.Session.get", _fake_get):
        _form_button(at, "Run").click().run(timeout=30)

    assert list(at.exception) == [], [str(e) for e in at.exception]
    return at


class SingleItemQuantityTest(unittest.TestCase):
    def test_reports_real_owned_quantity_not_hardcoded_one(self):
        at = _run_single_item_lookup("Police Vest")
        df_summary = at.session_state["analysis"]["df_summary"]
        row = df_summary[df_summary["item_id"] == POLICE_VEST_ID].iloc[0]
        self.assertEqual(int(row["my_quantity"]), len(OWNED_COPIES))

    def test_fetches_distinct_stats_per_owned_copy(self):
        at = _run_single_item_lookup("Police Vest")
        uid_stats = at.session_state["analysis"]["uid_stats"]

        self.assertEqual(set(uid_stats.keys()), set(OWNED_COPIES.keys()))
        for uid, expected_armor in OWNED_COPIES.items():
            self.assertEqual(uid_stats[uid]["armor"], expected_armor)
        # The equipped copy is excluded from ownership entirely, so its
        # stats should never have been fetched.
        self.assertNotIn(EQUIPPED_UID, uid_stats)


if __name__ == "__main__":
    unittest.main()
