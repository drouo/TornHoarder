"""Whole-page progress: the window fills from the bottom as a run goes, with a
big percentage in the right-hand margin riding the top of the fill, and a
status line at the bottom.

Drawn as a fixed overlay that ignores the mouse, so the page stays usable.
Each stage of a run owns a slice of the bar (see STAGES) so the fill moves
at a steady pace instead of racing through quick steps.
"""

from __future__ import annotations

from html import escape

import streamlit as st

# (start, end) share of the whole bar for each stage of a Run.
STAGES = {
    "inventory": (0.00, 0.10),
    "stats": (0.10, 0.25),
    "market": (0.25, 0.95),
    "compute": (0.95, 1.00),
}

_CSS = """
<style>
  .th-fill, .th-fill-pct, .th-fill-text { position: fixed; pointer-events: none; }
  .th-fill {
    left: 0; right: 0; bottom: 0; z-index: 999990;
    background: linear-gradient(to top, rgba(67,160,71,.34), rgba(67,160,71,.14));
    border-top: 3px solid rgba(124,179,66,.9);
    box-shadow: 0 -10px 40px rgba(124,179,66,.35);
  }
  /* In the empty margin right of the centered content, sitting on the fill line. */
  .th-fill-pct {
    right: 2.5vw; z-index: 999991;
    font: 900 clamp(48px, calc((100vw - 900px) / 2.8), 200px)/0.9 Arial, sans-serif;
    color: rgba(124,179,66,.6); letter-spacing: -0.04em; text-shadow: 0 0 24px rgba(124,179,66,.35);
  }
  @media (max-width: 1100px) { .th-fill-pct { display: none; } }
  .th-fill-text {
    left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 999992; max-width: 92vw;
    padding: 10px 22px; border-radius: 999px; background: rgba(0,0,0,.72); color: #fff;
    font: 600 18px/1.3 Arial, sans-serif; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    box-shadow: 0 4px 18px rgba(0,0,0,.4);
  }
</style>
"""


class PageProgress:
    """Fills the whole page as work progresses. Call update(), then clear()."""

    def __init__(self, placeholder=None) -> None:
        self._slot = placeholder if placeholder is not None else st.empty()
        self.fraction = 0.0

    def update(self, fraction: float, text: str = "") -> None:
        # Never move backwards: stages report their own 0..1 and may restart.
        self.fraction = max(self.fraction, min(max(float(fraction), 0.0), 1.0))
        pct = self.fraction * 100.0
        # The number rides the fill line, but stays on screen near the top.
        rides = min(pct, 78.0)
        status = f"{pct:.0f}%" + (f" · {text}" if text else "")
        self._slot.markdown(
            _CSS
            + f'<div class="th-fill" style="height:{pct:.2f}vh"></div>'
            + f'<div class="th-fill-pct" style="bottom:calc({rides:.2f}vh + 6px)">{pct:.0f}%</div>'
            + f'<div class="th-fill-text">{escape(status)}</div>',
            unsafe_allow_html=True,
        )

    def stage(self, name: str, done: float, total: float, text: str = "") -> None:
        """Progress within one stage of STAGES, mapped onto the whole bar."""
        start, end = STAGES[name]
        share = (done / total) if total else 1.0
        self.update(start + (end - start) * share, text)

    def clear(self) -> None:
        self._slot.empty()
