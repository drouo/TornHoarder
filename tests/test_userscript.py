import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.userscript import DEFAULT_CFG, build_userscript


class UserscriptTest(unittest.TestCase):
    def test_build_embeds_curve_and_fit(self):
        price_map = {
            26: {
                "name": "AK-47",
                "fast": 1000.0,
                "fair": 1100.0,
                "greedy": 1200.0,
                "curve": [[10.0, 900.0, 1000.0, 1100.0], [20.0, 1300.0, 1400.0, 1500.0]],
                "fit": {
                    "type": "Weapon",
                    "intercept": 5.0,
                    "coef": {"damage": 1.0, "accuracy": 1.0},
                    "base": {"damage": 50.0, "accuracy": 45.0},
                },
            },
            641: {"name": "Kevlar Gloves", "fast": 800.0, "fair": 900.0, "greedy": 950.0},
        }
        out = build_userscript(price_map, {}, cfg={"priceBasis": "greedy"})
        self.assertIn("interpolate", out)
        self.assertIn("qualityFromFit", out)
        self.assertIn('"curve"', out)
        self.assertIn('"fit"', out)

        # decode the embedded PRICE_MAP
        start = out.index("var PRICE_MAP = ") + len("var PRICE_MAP = ")
        end = out.index(";", start)
        decoded = json.loads(out[start:end])
        self.assertEqual(decoded["26"]["curve"][0], [10.0, 900.0, 1000.0, 1100.0])
        self.assertEqual(decoded["641"].get("fit"), None)
        self.assertEqual(decoded["641"]["greedy"], 950.0)

    def test_default_cfg(self):
        out = build_userscript({26: {"name": "X", "fast": 1.0, "fair": 2.0, "greedy": 3.0}}, {})
        self.assertIn('"priceBasis": "fair"', out)
        self.assertEqual(DEFAULT_CFG["fillQty"], True)


if __name__ == "__main__":
    unittest.main()