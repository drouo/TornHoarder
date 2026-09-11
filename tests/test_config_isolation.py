"""Regression test for the incident where an AppTest-driven test overwrote
a real saved Torn API key in ~/.tma_config.json with a placeholder test
value ("dummy-key"), because the "Remember API key" checkbox defaults to
checked whenever a key is already saved and the test submitted the form.

Proves two things:
1. The real ~/.tma_config.json is provably untouched by a full submit flow
   (an AppTest run that fills in a key and clicks Run, mirroring exactly
   what caused the incident).
2. The autouse isolate_local_config fixture (tests/conftest.py) is what's
   doing the protecting, by directly checking tma.config.CONFIG_PATH
   during a test.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from tma import config

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
REAL_CONFIG_PATH = Path.home() / ".tma_config.json"


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def _fake_get(self, url, headers=None, params=None, timeout=None):
    # Doesn't matter what it returns for this test — only that a full
    # submit-and-save flow runs, mirroring the incident. An error response
    # exercises the same save_api_key() call as a successful one would.
    return FakeResponse({"error": {"code": 2, "error": "Incorrect key"}})


def _text_input(at, key):
    [match] = [t for t in at.text_input if t.key == key]
    return match


def _radio(at, key):
    [match] = [r for r in at.radio if r.key == key]
    return match


def _form_button(at, label):
    [match] = [b for b in at.button if b.label == label]
    return match


class ConfigPathIsolationTest(unittest.TestCase):
    def test_config_path_is_redirected_during_tests(self):
        self.assertNotEqual(config.CONFIG_PATH, REAL_CONFIG_PATH)

    def test_full_submit_flow_never_touches_real_config_file(self):
        # Seed a pre-existing saved key (in the *isolated* config path) so
        # the "Remember API key" checkbox defaults to checked, same as the
        # real incident: it only overwrote a real key because one was
        # already saved. A fresh, never-configured environment wouldn't
        # have reproduced it.
        config.save_api_key("a-real-looking-key-should-not-be-overwritten")

        before = REAL_CONFIG_PATH.read_bytes() if REAL_CONFIG_PATH.exists() else None

        at = AppTest.from_file(str(APP))
        at.run(timeout=30)
        _radio(at, "source").set_value("Single item").run(timeout=30)
        _text_input(at, "single_query").set_value("Police Vest").run(timeout=30)
        _text_input(at, "api_key_input").set_value("dummy-key").run(timeout=30)

        with patch("requests.Session.get", _fake_get):
            _form_button(at, "Run").click().run(timeout=30)

        after = REAL_CONFIG_PATH.read_bytes() if REAL_CONFIG_PATH.exists() else None
        self.assertEqual(before, after, "a test run modified the real ~/.tma_config.json")

        # And confirm the save actually happened, just safely redirected —
        # a no-op fixture would make the assertion above trivially true.
        self.assertTrue(config.CONFIG_PATH.exists())
        self.assertIn("dummy-key", config.CONFIG_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
