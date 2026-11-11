"""Special finds tab: copies priced well above the item's standard curve price."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tma.market_enrichment import price_at_quality


def render_special_finds(ctx: dict) -> None:
    rows_out = ctx["rows_out"]
    strategy = ctx["strategy"]
    strategy_key = ctx["strategy_key"]

    cand_rows = []
    for ro in rows_out:
        if not ro.get("is_stat") or not ro["payload"].get("points"):
            continue
        p = ro["payload"]
        mid_q = p.get("median_quality")
        if not mid_q:
            continue
        std_cur = price_at_quality(p, float(mid_q))
        if not std_cur.get("derived"):
            continue
        std_price = std_cur.get(strategy_key) or 0
        copy_price = ro.get("List price") or 0
        if std_price <= 0 or copy_price <= 0:
            continue
        cand_rows.append(
            {
                "Sell": True,
                "Item": ro["Item"],
                "Damage %": ro.get("Damage %"),
                "Accuracy %": ro.get("Accuracy %"),
                "Armor": ro.get("Armor"),
                "Quality": ro.get("quality"),
                "Standard": int(round(std_price)),
                "Copy price": int(round(copy_price)),
                "+Premium %": (copy_price / std_price - 1) * 100.0,
            }
        )

    if not cand_rows:
        st.caption("No stat-priced copies to compare against the standard price.")
        return

    df_cand = pd.DataFrame(cand_rows).sort_values("+Premium %", ascending=False)
    min_p = st.slider(
        "Show copies priced at least N% above standard",
        0, 200, 5, 1, key="special_min_premium",
    )
    df_special = df_cand[df_cand["+Premium %"] >= min_p]
    if df_special.empty:
        st.caption("Nothing above that threshold.")
        return

    st.caption(
        f"{len(df_special)} of {len(df_cand)} copies priced above the item's standard "
        f"({strategy}). Uncheck a row to skip it."
    )
    chosen = st.data_editor(
        df_special.reset_index(drop=True),
        width="stretch",
        hide_index=True,
        key="special_editor",
        column_config={
            "Sell": st.column_config.CheckboxColumn(),
            "Item": st.column_config.Column(disabled=True),
            "Damage %": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "Accuracy %": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "Armor": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "Quality": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "Standard": st.column_config.NumberColumn(format="compact", disabled=True),
            "Copy price": st.column_config.NumberColumn(format="compact", disabled=True),
            "+Premium %": st.column_config.NumberColumn(format="%.1f", disabled=True),
        },
    )
    n_sel = int(chosen["Sell"].sum())
    if st.button(f"Copy special listings ({n_sel})"):
        lines = []
        for _, row in chosen[chosen["Sell"]].iterrows():
            price = row.get("Copy price")
            if price is None:
                continue
            lines.append(f"{row['Item']} x1 @ ${int(round(float(price))):,}")
        st.session_state["special_listing"] = "\n".join(lines)

    if st.session_state.get("special_listing"):
        st.code(st.session_state["special_listing"], language=None)
        st.download_button(
            "Download special .txt",
            st.session_state["special_listing"],
            file_name="torn_special_listings.txt",
            mime="text/plain",
            key="download_special_listing",
        )
