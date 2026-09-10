"""Tests for the Bulk sell tab.

streamlit.testing.v1.AppTest (this Streamlit version) doesn't expose
data_editor as an inspectable/interactive element, so cell-level edits
(checking/unchecking "Sell") aren't simulate-able headlessly. mundane_rows()
and bulk_row() are plain functions specifically so the pricing logic is
still directly unit-testable; the AppTest run covers end-to-end wiring with
the default (everything checked) state.
"""

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))

from streamlit.testing.v1 import AppTest

from app.ui.bulk_sell import bulk_row, mundane_rows

HARNESS = Path(__file__).resolve().parent / "fixtures" / "render_results_harness.py"


def _button(at, key):
    matches = [b for b in at.button if b.key == key]
    assert matches, f"no button with key={key!r}"
    return matches[0]


class MundaneRowsTest(unittest.TestCase):
    def test_excludes_stat_priced_rows(self):
        rows = [{"Item": "Kevlar Gloves", "is_stat": False}, {"Item": "AK-47", "is_stat": True}]
        self.assertEqual(mundane_rows(rows), [{"Item": "Kevlar Gloves", "is_stat": False}])


class BulkRowTest(unittest.TestCase):
    def test_computes_pct_of_basis(self):
        row = bulk_row({"Item": "Plushie", "My quantity": 4, "Fair price": 900.0}, pct=75, basis="Fair price")
        self.assertEqual(row, {"Sell": True, "Item": "Plushie", "My quantity": 4, "Fair price": 900.0, "Bulk price": 675})

    def test_missing_basis_price_yields_none(self):
        row = bulk_row({"Item": "X", "My quantity": 1, "Fair price": None}, pct=75, basis="Fair price")
        self.assertIsNone(row["Bulk price"])


class BulkSellAppTest(unittest.TestCase):
    def test_default_state_builds_listing_for_mundane_items_only(self):
        at = AppTest.from_file(str(HARNESS))
        at.run(timeout=30)
        self.assertEqual(list(at.exception), [])

        _button(at, "bulk_sell_build").click().run(timeout=30)
        self.assertEqual(list(at.exception), [])

        # Fixture: Kevlar Gloves (plain, Fair price 900.0, qty 3) and AK-47
        # (stat-priced, excluded). Default 75% of Fair price -> 675.
        self.assertEqual(at.session_state["bulk_listing_text"], "Kevlar Gloves x3 @ $675")


if __name__ == "__main__":
    unittest.main()
