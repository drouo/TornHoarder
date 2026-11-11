import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.fmt import money


class MoneyTest(unittest.TestCase):
    def test_three_significant_figures_with_suffix(self):
        cases = {
            0: "$0", 950: "$950", 1000: "$1k", 1201: "$1.2k", 15568: "$15.6k", 100_000: "$100k", 473_999: "$474k",
            1_234_567: "$1.23m", 15_568_893: "$15.6m", 450_000_000: "$450m", 2_954_452_646: "$2.95b", -1500: "-$1.5k",
        }
        for v, want in cases.items():
            self.assertEqual(money(v), want, v)

    def test_rounding_up_moves_to_the_next_unit(self):
        self.assertEqual(money(999.6), "$1k")
        self.assertEqual(money(999_950), "$1m")

    def test_blank_for_missing(self):
        self.assertEqual((money(None), money(float("nan")), money("x")), ("", "", ""))


if __name__ == "__main__":
    unittest.main()
