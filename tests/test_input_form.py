"""Headless test for the input form's conditional widgets.

Regression test for a bug where selecting "Single item" (or "Custom…"
scope) didn't reveal its matching input(s) until after a first, doomed
Run click — because the selector radios lived inside st.form(), and
widgets inside a form only trigger a rerun on submit. Fixed by moving
the source/scope selectors (and everything that depends on them) above
the form, so choosing one immediately reruns the script and reveals the
right inputs.
"""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


class InputFormConditionalWidgetsTest(unittest.TestCase):
    def test_selecting_single_item_reveals_its_inputs_without_submitting(self):
        at = AppTest.from_file(str(APP))
        at.run(timeout=30)
        self.assertEqual(list(at.exception), [])

        [source_radio] = [r for r in at.radio if r.key == "source"]
        source_radio.set_value("Single item").run(timeout=30)
        self.assertEqual(list(at.exception), [])

        self.assertTrue(any(t.key == "single_query" for t in at.text_input))
        self.assertTrue(any(t.key == "single_uid" for t in at.text_input))

    def test_selecting_custom_scope_reveals_category_picker_without_submitting(self):
        at = AppTest.from_file(str(APP))
        at.run(timeout=30)

        [scope_radio] = [r for r in at.radio if r.key == "inventory_scope"]
        scope_radio.set_value("Custom…").run(timeout=30)
        self.assertEqual(list(at.exception), [])

        self.assertTrue(any(m.key == "inventory_categories" for m in at.multiselect))


if __name__ == "__main__":
    unittest.main()
