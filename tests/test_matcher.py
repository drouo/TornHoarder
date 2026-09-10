import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.inventory_matcher import load_dictionary, match_inventory


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.csv = Path(self.tmpdir.name) / "dict.csv"
        self.csv.write_text(
            "key,id\nAK-47,26\nGold Plated AK-47,26\nKevlar Gloves,641\n\n",
            encoding="utf-8-sig",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_dictionary(self):
        mapping = load_dictionary(self.csv)
        self.assertEqual(mapping["AK-47"], 26)
        self.assertEqual(mapping["Gold Plated AK-47"], 26)

    def test_match_aggregates(self):
        text = "AK-47\nx2\nKevlar Gloves\nx1\n"
        result = match_inventory(text, self.csv)
        self.assertEqual(len(result.matched), 2)
        by_name = {m.name: m for m in result.matched}
        self.assertEqual(by_name["AK-47"].qty, 2)
        self.assertEqual(by_name["Kevlar Gloves"].qty, 1)
        self.assertEqual(result.unmatched, [])

    def test_unmatched(self):
        result = match_inventory("Unknown Item\nx3\n", self.csv)
        self.assertEqual(result.matched, [])
        self.assertEqual(result.unmatched, [("Unknown Item", 3)])

    def test_unmatched_same_line_qty(self):
        result = match_inventory("Mystery Widget x5\n", self.csv)
        self.assertEqual(result.unmatched, [("Mystery Widget", 5)])


if __name__ == "__main__":
    unittest.main()