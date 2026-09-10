"""Autofill script tab: generates and downloads the Tampermonkey userscript
that fills prices directly on Torn's Add Listing page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tma.quality import PREMIUM_THRESHOLD, base_stats_map
from tma.userscript import build_userscript

from ..paths import AUTOFILL_PATH


def render_autofill_export(an: dict, ctx: dict) -> None:
    df_summary = an["df_summary"]
    payload_by_item = ctx["payload_by_item"]
    profiles = an.get("profiles") or {}

    price_map = {}
    for _, r in df_summary.iterrows():
        iid = r.get("item_id")
        if iid is None or pd.isna(iid):
            continue
        lu = {
            "name": str(r.get("item_name", "")),
            "fast": float(r.get("fast_sell_price") or 0.0),
            "fair": float(r.get("fair_price") or 0.0),
            "greedy": float(r.get("greedy_price") or 0.0),
        }
        ptool = payload_by_item.get(int(iid)) or {}
        pts = ptool.get("points") or []
        curve = [
            [float(p["q"]), p.get("fast"), p.get("fair"), p.get("greedy")]
            for p in pts
            if p.get("fast") is not None or p.get("fair") is not None or p.get("greedy") is not None
        ]
        if curve:
            lu["curve"] = curve
        fit = ptool.get("fit") or {}
        if fit.get("type"):
            lu["fit"] = {
                "type": fit["type"],
                "intercept": fit.get("intercept"),
                "coef": fit.get("coef") or {},
                "base": fit.get("base") or {},
            }
        price_map[int(iid)] = lu

    userjs = build_userscript(price_map, base_stats_map(profiles))
    try:
        AUTOFILL_PATH.write_text(userjs, encoding="utf-8")
        st.caption(
            "Saved to `data/tma_autofill.user.js` — Tampermonkey auto-updates from "
            "`http://localhost:9999/tma_autofill.user.js` when this page refreshes and the "
            "update server is running."
        )
    except OSError as exc:
        st.caption(f"Could not write update copy: {exc}")

    st.download_button(
        "Download Torn autofill script (.user.js)",
        userjs,
        file_name="tma_autofill.user.js",
        mime="application/javascript",
        key="download_userjs",
    )
    with st.expander("About the autofill script"):
        st.markdown(
            f"Installs in [Tampermonkey](https://www.tampermonkey.net/). On the Torn **Add Listing** "
            f"page it fills each row's price from your suggested prices. For weapons/armor it reads the "
            f"row's stat rolls, computes the copy's quality from the app's fitted market model, and prices "
            f"it on the item's quality curve (no extra premium — the curve already reflects quality). "
            f"Copies with quality **≥ {PREMIUM_THRESHOLD:.0f}%** are highlighted. "
            "You can change the price basis and thresholds in the `CFG` block at the top of the script."
        )
