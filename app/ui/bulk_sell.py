"""Bulk sell tab: quickly discount a pile of mundane (non stat-priced) items
to a flat percentage of a chosen price basis, then copy the listing text.

Deliberately a separate small table/button pair (same pattern as Special
finds) rather than writing back into the main pricing table's List price
column — Streamlit's data_editor rejects programmatic session_state writes
to its own widget key, and routing through it would also fight the main
table's per-copy stat pricing for weapon/armor rows.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .pricing_table import LIST_BASIS_OPTIONS


def mundane_rows(rows_out: list[dict]) -> list[dict]:
    """Plain (non stat-priced) rows — the candidates for a flat bulk discount."""
    return [r for r in rows_out if not r.get("is_stat")]


def bulk_row(r: dict, pct: float, basis: str) -> dict:
    basis_price = r.get(basis) or 0
    return {
        "Sell": True,
        "Item": r["Item"],
        "My quantity": r["My quantity"],
        basis: basis_price,
        "Bulk price": int(round(basis_price * pct / 100.0)) if basis_price else None,
    }


def render_bulk_sell(ctx: dict) -> None:
    mundane = mundane_rows(ctx["rows_out"])

    if not mundane:
        st.caption("Nothing here — every priced row is a stat-priced weapon/armor copy.")
        return

    st.caption(
        "For a pile of basic items you just want gone: pick a discount, uncheck anything you'd "
        "rather keep at full price, then copy the listing text below."
    )

    c1, c2 = st.columns([1, 1])
    with c1:
        pct = st.number_input("% of basis", min_value=1, max_value=200, value=75, step=5, key="bulk_pct")
    with c2:
        basis = st.selectbox("Basis", LIST_BASIS_OPTIONS, index=1, key="bulk_basis")

    df = pd.DataFrame([bulk_row(r, pct, basis) for r in mundane])

    edited = st.data_editor(
        df,
        width="stretch",
        hide_index=True,
        key="bulk_sell_editor",
        column_config={
            "Sell": st.column_config.CheckboxColumn(),
            "Item": st.column_config.Column(disabled=True),
            "My quantity": st.column_config.NumberColumn(format="%,.0f", disabled=True),
            basis: st.column_config.NumberColumn(format="%,.0f", disabled=True),
            "Bulk price": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        },
    )

    n_sel = int(edited["Sell"].sum())
    if st.button(f"Copy bulk listing ({n_sel} item{'s' if n_sel != 1 else ''})", key="bulk_sell_build"):
        lines = []
        for _, row in edited[edited["Sell"]].iterrows():
            price = row.get("Bulk price")
            qty = row.get("My quantity")
            if price is None or pd.isna(price) or not qty:
                continue
            lines.append(f"{row['Item']} x{int(qty)} @ ${int(price):,}")
        st.session_state["bulk_listing_text"] = "\n".join(lines)

    if st.session_state.get("bulk_listing_text"):
        st.code(st.session_state["bulk_listing_text"], language=None)
        st.download_button(
            "Download bulk .txt",
            st.session_state["bulk_listing_text"],
            file_name="torn_bulk_listings.txt",
            mime="text/plain",
            key="download_bulk_listing",
        )
