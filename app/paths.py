"""Shared filesystem paths, imported by both the entry point and app/ui/*."""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
DATA_DIR = ROOT_DIR / "data"

DICT_PATH = (DATA_DIR / "torn_item_dictionary.csv").resolve()
PROFILES_PATH = (DATA_DIR / "torn_item_profiles.json").resolve()
AUTOFILL_PATH = DATA_DIR / "tornhoarder.user.js"
SHOP_BUYS_PATH = DATA_DIR / "shop_buys.json"
OC_ITEMS_PATH = DATA_DIR / "oc_items.json"
UID_STATS_PATH = DATA_DIR / "uid_stats.json"
PRICE_HISTORY_PATH = DATA_DIR / "price_history.csv"
MUSEUM_PATH = DATA_DIR / "museum_sets.json"
CITYSHOP_STOCK_PATH = DATA_DIR / "cityshop_stock.csv"
STOCK_STATUS_PATH = DATA_DIR / "stock_status.json"
FOREIGN_STOCK_PATH = DATA_DIR / "foreign_stock.csv"
FOREIGN_UPDATES_PATH = DATA_DIR / "foreign_updates.csv"
TRAVEL_PRICES_PATH = DATA_DIR / "travel_prices.json"
SHOP_PLAN_PATH = DATA_DIR / "shop_plan.json"
