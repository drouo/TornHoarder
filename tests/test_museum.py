"""Museum sets (tma.museum): exchange-vs-sell comparison and the sell-plan holds it adds."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ui.sell_plan import plan_rows
from tma.museum import evaluate_sets, museum_holds, parse_museum
from tma.sell_plan import SellRules

SETS = parse_museum({"museum": [
    {"name": "Plushie", "points": 10, "items": [186, 187, 215]},
    {"name": "Flower", "points": 10, "items": [260, 263]},
    {"name": "Empty", "points": 0, "items": [1]},
]})
NAMES = {186: "Sheep Plushie", 187: "Teddy Bear Plushie", 215: "Kitten Plushie", 260: "Dahlia", 263: "Crocus"}
REF = {186: 500.0, 187: 500.0, 215: 500.0, 260: 4000.0, 263: 6000.0}


class MuseumTest(unittest.TestCase):
    def test_parse_skips_sets_without_points(self):
        self.assertEqual([s["name"] for s in SETS], ["Plushie", "Flower"])

    def test_exchange_vs_sell_and_next_set_needs(self):
        owned = {186: 3, 187: 2, 215: 2, 260: 1}
        unit = {186: 500.0, 187: 480.0, 215: 520.0, 260: 3900.0}
        plush, flower = evaluate_sets(SETS, owned, unit, REF, NAMES, point_price=45_000.0)
        # 10 points x $45k = $450k vs (500 + 480 + 520) x 0.95 = $1,425.
        self.assertEqual((plush["complete"], plush["better"], plush["exchange_value"]), (2, "Exchange", 450_000.0))
        self.assertAlmostEqual(plush["sell_value"], 1425.0)
        self.assertEqual((plush["missing"], plush["missing_cost"]), (["Teddy Bear Plushie", "Kitten Plushie"], 1000.0))
        # No Crocus: no complete set; the Crocus is valued at its reference price.
        self.assertEqual((flower["complete"], flower["missing"]), (0, ["Crocus"]))
        self.assertAlmostEqual(flower["sell_value"], (3900.0 + 6000.0) * 0.95)

    def test_no_point_price_means_no_verdict(self):
        (plush,) = evaluate_sets(SETS[:1], {186: 1}, {}, REF, NAMES, point_price=None)
        self.assertIsNone(plush["better"])

    def test_holds_keep_one_piece_per_complete_set_only_when_exchange_wins(self):
        owned = {186: 3, 187: 2, 215: 2}
        cheap = evaluate_sets(SETS, owned, {}, REF, NAMES, point_price=1.0)
        self.assertEqual(museum_holds(cheap), {})
        holds = museum_holds(evaluate_sets(SETS, owned, {}, REF, NAMES, point_price=45_000.0))
        self.assertEqual({k: v[0] for k, v in holds.items()}, {186: 2, 187: 2, 215: 2})

    def test_plan_keeps_set_pieces_and_sells_the_extras_unless_you_set_keeping(self):
        def row(iid, qty):
            return {"item_id": iid, "Item": NAMES[iid], "My quantity": qty, "Fast-sell price": 500.0,
                    "Fair price": 550.0, "Greedy price": 600.0, "is_stat": False, "uid": None}

        profiles = {str(i): {"id": i, "type": "Plushie"} for i in (186, 187, 215)}
        holds = museum_holds(evaluate_sets(SETS, {186: 3, 187: 2, 215: 2}, {}, REF, NAMES, point_price=45_000.0))
        rows_out = [row(186, 3), row(187, 2), row(215, 2)]
        sheep, teddy, _ = plan_rows(rows_out, {}, {}, profiles, SellRules(), {}, museum=holds)
        self.assertEqual((sheep["Keeping"], sheep["Selling"]), (2, 1))
        self.assertIn("museum: 2 Plushie set(s)", sheep["Why"])
        self.assertEqual((teddy["Keeping"], teddy["Plan"]), (2, "Keep"))
        # Your own Keeping number wins.
        sheep = plan_rows(rows_out, {}, {}, profiles, SellRules(), {}, reserves={186: 0}, museum=holds)[0]
        self.assertEqual(sheep["Keeping"], 0)


if __name__ == "__main__":
    unittest.main()
