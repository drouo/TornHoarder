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


@pytest.fixture(autouse=True)
def no_catalog_refresh(monkeypatch):
    """The app refreshes data/ from torn/items when the catalog is stale. Under
    test that would be a network call — or, with a mocked session, overwrite
    the real data/ files — so the app-facing entry point is a no-op here.
    tests/test_catalog.py exercises the real function against tmp paths."""
    from tma import catalog

    monkeypatch.setattr(catalog, "refresh_catalog_if_stale", lambda *a, **k: (False, None))


@pytest.fixture(autouse=True)
def isolate_autofill_output(tmp_path, monkeypatch):
    """The Autofill tab writes data/tornhoarder.user.js — the very file the
    update server hands to Tampermonkey — and starts that server on :9999.
    Rendering it from fixture data used to overwrite the user's real script
    with the two test items, so tests write to tmp and never bind the port."""
    from app.ui import autofill_export

    monkeypatch.setattr(autofill_export, "AUTOFILL_PATH", tmp_path / "tornhoarder.user.js")
    monkeypatch.setattr(autofill_export, "_ensure_update_server", lambda *a, **k: (False, "http://test/"))


@pytest.fixture(autouse=True)
def isolate_shop_knowledge(tmp_path, monkeypatch):
    """The Sell plan tab reads data/shop_buys.json (learned from real shop
    runs); tests must neither depend on nor touch the user's copy."""
    from app.ui import sell_plan

    monkeypatch.setattr(sell_plan, "SHOP_BUYS_PATH", tmp_path / "shop_buys.json")
