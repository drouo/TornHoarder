"""Headless smoke test for the app/ui/* split (M3/M4 restructure).

Drives render_results() through streamlit.testing.v1.AppTest with a
synthetic analysis dict covering both a plain item and a stat-priced
weapon copy, so the pricing table / special finds / autofill / diagnostics
tabs all actually execute. Catches wiring bugs (bad key, circular import,
wrong ctx field) that a plain `import` wouldn't.
"""

import sys
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

HARNESS = Path(__file__).resolve().parent / "fixtures" / "render_results_harness.py"


class RenderResultsSmokeTest(unittest.TestCase):
    def test_renders_without_exceptions(self):
        at = AppTest.from_file(str(HARNESS))
        at.run(timeout=30)
        self.assertEqual(list(at.exception), [], msg=[str(e) for e in at.exception])

    def test_tabs_present(self):
        at = AppTest.from_file(str(HARNESS))
        at.run(timeout=30)
        tab_labels = {t.proto.label for t in at.tabs}
        self.assertEqual(
            tab_labels,
            {"Pricing table", "Special finds", "Autofill script", "Diagnostics"},
        )


if __name__ == "__main__":
    unittest.main()
