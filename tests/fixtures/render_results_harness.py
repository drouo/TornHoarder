"""Not a test file itself — a minimal Streamlit script that feeds a synthetic
`analysis` dict into `render_results`, so `tests/test_app_ui_smoke.py` can
drive it headlessly via `streamlit.testing.v1.AppTest` and catch wiring
errors across app/ui/* (wrong key, bad import, etc.) without a browser.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(SRC_DIR))

import pandas as pd

from app.ui.results import render_results
from tma.inventory import InventoryEntry

WEAPON_PAYLOAD = {
    "stat_type": "Weapon",
    "points": [
        {"q": 0.0, "n": 5, "fast": 900.0, "fair": 1000.0, "greedy": 1100.0},
        {"q": 50.0, "n": 5, "fast": 1400.0, "fair": 1500.0, "greedy": 1600.0},
    ],
    "median_quality": 25.0,
    "median_stats": {"damage": 52.0, "accuracy": 46.0, "armor": None},
    "fit": {
        "type": "Weapon",
        "intercept": 25.0,
        "coef": {"damage": 1.0, "accuracy": 1.0},
        "base": {"damage": 50.0, "accuracy": 45.0},
        "n": 20,
    },
    "n_quality_listings": 10,
}

df_summary = pd.DataFrame(
    [
        {
            "item_id": 641,
            "item_name": "Kevlar Gloves",
            "item_type": "Defensive",
            "average_price_reported": 900,
            "my_quantity": 3,
            "num_listings": 10,
            "num_suspected_anchors": 0,
            "fast_sell_price": 850.0,
            "fair_price": 900.0,
            "greedy_price": 950.0,
            "clean_median_price": 900.0,
            "clean_q1_price": 880.0,
            "clean_q3_price": 950.0,
            "copy_tool": {},
            "stat_tier": "—",
            "market_price_ref": None,
            "stat_implied": None,
        },
        {
            "item_id": 26,
            "item_name": "AK-47",
            "item_type": "Primary",
            "average_price_reported": 1200,
            "my_quantity": 1,
            "num_listings": 15,
            "num_suspected_anchors": 1,
            "fast_sell_price": 1000.0,
            "fair_price": 1100.0,
            "greedy_price": 1200.0,
            "clean_median_price": 1100.0,
            "clean_q1_price": 1050.0,
            "clean_q3_price": 1200.0,
            "copy_tool": WEAPON_PAYLOAD,
            "stat_tier": "Premium",
            "market_price_ref": 1300.0,
            "stat_implied": 1600.0,
        },
    ]
)

analysis = {
    "df_summary": df_summary,
    "warnings": [
        {
            "item_id": 26,
            "item_name": "AK-47",
            "my_quantity": 1,
            "fair_price": 1100.0,
            "market_price": 1300.0,
            "implied_value": 1600.0,
            "tier": "Premium",
            "ratio": 1300.0 / 1100.0,
            "elevated": True,
        }
    ],
    "df_parsed": pd.DataFrame([{"name": "Kevlar Gloves", "id": 641, "quantity": 3}, {"name": "AK-47", "id": 26, "quantity": 1}]),
    "unmatched": [("Some Unknown Item", 2)],
    "df_market": pd.DataFrame([{"item_id": 641, "item_name": "Kevlar Gloves", "price_1": 900, "amount_1": 5}]),
    "df_long": pd.DataFrame([{"item_id": 641, "item_name": "Kevlar Gloves", "price": 900.0, "quantity": 5.0}]),
    "profiles": {
        "641": {"id": 641, "name": "Kevlar Gloves", "type": "Armor", "sell_price": 1000, "vendor": "Bits 'n' Bobs"},
        "26": {"id": 26, "name": "AK-47", "type": "Weapon", "category": "Primary"},
    },
    "entries": [InventoryEntry(item_id=26, name="AK-47", amount=1, equipped=False, uid="123")],
    "uid_stats": {"123": {"damage": 55.0, "accuracy": 48.0, "armor": None, "quality": 30.0}},
}

render_results(analysis)
