"""Pytest-wide safety net: never let a test read or write the user's real
~/.tma_config.json.

A prior version of the AppTest-driven tests (which run the actual
app/streamlit_app.py script end-to-end) didn't isolate this. The "Remember
API key" checkbox defaults to checked whenever a key is already saved, so
a test that filled in a placeholder key and clicked Run overwrote a real
saved Torn API key with that placeholder. This fixture makes that
impossible: every test — no matter how it's written — gets its own
throwaway config path for the whole test session, restored automatically
afterward.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
for p in (str(ROOT_DIR), str(ROOT_DIR / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest


@pytest.fixture(autouse=True)
def isolate_local_config(tmp_path, monkeypatch):
    from tma import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".tma_config.json")
