"""Diagnostics tab: raw/intermediate tables useful for debugging a run."""

from __future__ import annotations

import pandas as pd
import streamlit as st

_VIEWS = ("Parsed items", "Unmatched items", "Market data (wide)", "Market data (long)", "Pricing diagnostics")


def render_diagnostics(an: dict, df_summary: pd.DataFrame) -> None:
    view = st.radio("Table", _VIEWS, horizontal=True, key="diagnostics_view")

    if view == "Parsed items":
        st.dataframe(an["df_parsed"], width="stretch", hide_index=True)
    elif view == "Unmatched items":
        if an["unmatched"]:
            df_unmatched = (
                pd.DataFrame(an["unmatched"], columns=["name", "qty"])
                .sort_values("name")
                .reset_index(drop=True)
            )
            st.dataframe(df_unmatched, width="stretch", hide_index=True)
        else:
            st.info("No unmatched items.")
    elif view == "Market data (wide)":
        st.dataframe(an["df_market"], width="stretch", hide_index=True)
    elif view == "Market data (long)":
        st.dataframe(an["df_long"], width="stretch", hide_index=True)
    else:
        st.dataframe(df_summary, width="stretch", hide_index=True)
