"""The page frame: API key in the sidebar, the status board, and the section
buttons that pick which tools render."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


def button(at, key):
    [b] = [b for b in at.button if b.key == key]
    return b


class AppFrameTest(unittest.TestCase):
    def setUp(self):
        self.at = AppTest.from_file(str(APP))
        self.at.run(timeout=30)
        self.assertEqual(list(self.at.exception), [], [str(e) for e in self.at.exception])

    def text(self):
        return "\n".join(m.value for m in self.at.markdown) + "\n".join(c.value for c in self.at.caption)

    def test_opens_on_sell_with_run_and_the_status_board(self):
        self.assertTrue(any(b.label == "Run" for b in self.at.button))
        self.assertIn("**Status**", self.text())
        self.assertIn("Click Run to price your items.", self.text())
        self.assertEqual(button(self.at, "nav_Sell").proto.type, "primary")
        self.assertTrue(any(t.key == "api_key_input" for t in self.at.sidebar.text_input))

    def test_without_a_key_run_is_disabled_and_the_sidebar_says_so(self):
        [run] = [b for b in self.at.button if b.label == "Run"]
        self.assertTrue(run.disabled)
        self.assertTrue(any("Enter your API key" in w.value for w in self.at.sidebar.warning))

    def test_section_buttons_switch_what_renders(self):
        button(self.at, "nav_Travel").click().run(timeout=30)
        self.assertEqual(list(self.at.exception), [])
        self.assertFalse(any(b.label == "Run" for b in self.at.button))
        self.assertTrue(any(s.value == "Where to fly" for s in self.at.subheader))
        button(self.at, "nav_Restock").click().run(timeout=30)
        self.assertTrue(any(s.value.startswith("Keep stocked") for s in self.at.subheader))
        self.assertTrue(any(s.value == "Restock watch" for s in self.at.subheader))
        button(self.at, "nav_Help").click().run(timeout=30)
        self.assertIn("How to use the app", self.text())

    def test_a_status_row_jumps_to_its_section(self):
        [go] = [b for b in self.at.button if b.key and b.key.startswith("go_Where to fly_")]
        go.click().run(timeout=30)
        self.assertEqual(list(self.at.exception), [])
        self.assertEqual(button(self.at, "nav_Travel").proto.type, "primary")


if __name__ == "__main__":
    unittest.main()
