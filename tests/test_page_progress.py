import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ui.page_progress import PageProgress


class FakeSlot:
    def __init__(self):
        self.html = None

    def markdown(self, html, unsafe_allow_html=False):
        self.html = html

    def empty(self):
        self.html = None


def height(slot):
    return float(re.search(r'th-fill" style="height:([0-9.]+)vh', slot.html).group(1))


class PageProgressTest(unittest.TestCase):
    def test_stages_map_onto_their_slice_of_the_page(self):
        slot = FakeSlot()
        page = PageProgress(slot)
        page.stage("inventory", 1, 2, "Pulling")
        self.assertEqual(height(slot), 5.0)
        page.stage("market", 50, 100, "Pricing 50/100 · <Axe>")
        self.assertEqual(height(slot), 60.0)
        self.assertIn(">60%<", slot.html)
        self.assertIn("60% · Pricing 50/100 · &lt;Axe&gt;", slot.html)
        # The big number rides the top of the fill.
        self.assertIn("bottom:calc(60.00vh + 6px)", slot.html)

    def test_never_moves_backwards_and_clears(self):
        slot = FakeSlot()
        page = PageProgress(slot)
        page.stage("market", 1, 1)
        page.stage("market", 0, 10, "Retrying")  # a retry pass restarts its own count
        self.assertEqual(height(slot), 95.0)
        page.clear()
        self.assertIsNone(slot.html)


if __name__ == "__main__":
    unittest.main()
