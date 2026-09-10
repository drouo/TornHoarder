"""Shared filesystem paths, imported by both the entry point and app/ui/*."""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
DATA_DIR = ROOT_DIR / "data"

DICT_PATH = (DATA_DIR / "torn_item_dictionary.csv").resolve()
PROFILES_PATH = (DATA_DIR / "torn_item_profiles.json").resolve()
AUTOFILL_PATH = DATA_DIR / "tma_autofill.user.js"
