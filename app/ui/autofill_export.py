"""Autofill script tab: generates and downloads the Tampermonkey userscript
that fills prices directly on Torn's Add Listing page.
"""

from __future__ import annotations

import json
import threading

import pandas as pd
import streamlit as st

from tma.autoupdate_server import DEFAULT_PORT, build_server
from tma.quality import PREMIUM_THRESHOLD, base_stats_map
from tma.userscript import build_userscript

from ..paths import AUTOFILL_PATH, SHOP_BUYS_PATH, STOCK_STATUS_PATH, TRAVEL_PRICES_PATH
from .stock_minimums import status_json


@st.cache_resource
def _ensure_update_server(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """Start the Tampermonkey update server on a background thread, once per app process.

    st.cache_resource guarantees this body runs at most once for the life of the
    Streamlit process, so repeated script reruns never try to rebind the port.
    """
    url = f"http://127.0.0.1:{port}/{AUTOFILL_PATH.name}"
    try:
        server = build_server(AUTOFILL_PATH, port=port, reports_path=SHOP_BUYS_PATH, status_path=STOCK_STATUS_PATH,
                              travel_path=TRAVEL_PRICES_PATH)
    except OSError:
        # Port already bound — most likely this same server from a prior process
        # (e.g. `tools/update_server.py` run by hand), so the URL is still live.
        return True, url
    threading.Thread(target=server.serve_forever, daemon=True, name="tma-autoupdate-server").start()
    return True, url


def render_autofill_export(an: dict, ctx: dict, plan_ctx: dict) -> None:
    df_summary = an["df_summary"]
    payload_by_item = ctx["payload_by_item"]
    profiles = an.get("profiles") or {}
    item_plans = plan_ctx["item_plans"]
    rules = plan_ctx["rules"]

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
        lu.update(item_plans.get(int(iid)) or {})
        price_map[int(iid)] = lu

    userjs = build_userscript(
        price_map,
        base_stats_map(profiles),
        cfg={"dumpUndercutPct": rules.dump_undercut_pct, "topMinQuality": rules.top_min_quality,
             "keepCash": rules.keep_cash, "ipecacOver": rules.ipecac_over},
        shop_run=plan_ctx.get("shop_run"),
        stock=json.loads(status_json()),
    )
    write_ok = True
    try:
        AUTOFILL_PATH.write_text(userjs, encoding="utf-8")
    except OSError as exc:
        write_ok = False
        st.caption(f"Could not write update copy: {exc}")

    if write_ok:
        server_ok, update_url = _ensure_update_server()
        if server_ok:
            st.success(f"**[Click to load into Tampermonkey]({update_url})**")
            st.caption(
                "Click it — Tampermonkey should open its install/update dialog. Some Tampermonkey "
                "setups don't auto-detect scripts served from a local address like this one; if "
                "clicking just shows a blank Tampermonkey web page instead of an install prompt, "
                "use the download button below instead."
            )
        else:
            st.warning(f"Couldn't start the local update server ({update_url}). Use the download button below.")

    st.download_button(
        "Download Torn autofill script (.user.js) — manual install",
        userjs,
        file_name="tornhoarder.user.js",
        mime="application/javascript",
        key="download_userjs",
    )
    st.caption(
        "Manual install: after downloading, open the Tampermonkey Dashboard "
        "(extension icon → **Dashboard**) and drag the downloaded file onto that page, "
        "or use its **Utilities → Import from file**. Re-download and re-import after each "
        "run to refresh the prices."
    )
    with st.expander("About the autofill script"):
        st.markdown(
            f"Installs in [Tampermonkey](https://www.tampermonkey.net/). On the Torn **Add Listing** "
            f"page it fills each row's price and quantity from the **Sell plan** tab, and leaves Shop / Keep "
            f"rows blank. For weapons/armor it reads the row's stat rolls, computes the copy's quality from "
            f"the app's fitted market model, and prices it on the item's quality curve for that plan; copies "
            f"with quality **≥ {rules.top_min_quality:.0f}** are listed at Top dollar unless you chose a plan "
            f"yourself. Copies with quality **≥ {PREMIUM_THRESHOLD:.0f}%** are highlighted."
        )
