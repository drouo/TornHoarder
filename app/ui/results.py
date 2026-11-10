"""Top-level results renderer: one tab per concern instead of one long scroll."""

from __future__ import annotations

import streamlit as st

from .autofill_export import render_autofill_export
from .diagnostics import render_diagnostics
from .pricing_table import render_pricing_table, render_warnings_banner
from .sell_plan import render_sell_plan
from .special_finds import render_special_finds


def render_results(an: dict) -> None:
    df_summary = an["df_summary"]
    warnings = an["warnings"]

    st.subheader("Price overview")
    render_warnings_banner(warnings)

    tab_plan, tab_pricing, tab_special, tab_autofill, tab_diag = st.tabs(
        ["Sell plan", "Pricing table", "Special finds", "Autofill script", "Diagnostics"]
    )

    # The pricing table builds the per-copy rows everything else consumes,
    # so it renders first even though Sell plan is the first tab shown.
    with tab_pricing:
        ctx = render_pricing_table(an)

    with tab_plan:
        plan_ctx = render_sell_plan(an, ctx)

    with tab_special:
        render_special_finds(ctx)

    with tab_autofill:
        render_autofill_export(an, ctx, plan_ctx)

    with tab_diag:
        render_diagnostics(an, df_summary)
