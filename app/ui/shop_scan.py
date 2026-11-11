"""Scan shops: city-shop items in stock now that resell at a profit."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from tma.fmt import money
from tma.http_client import session_for_requests, shared_bucket
from tma.market_fetch import fetch_all_item_markets
from tma.shop_flips import fetch_city_shops, log_stock, scan_flips

from .. import paths


def render_shop_scan(api_key: str) -> None:
    with st.expander("Shop flips: buy in city shops, resell on the market"):
        st.caption(
            "Checks every city shop's current stock against live market listings (about 110 API calls, a minute "
            "or so). Profit is $1 under the cheapest listing after Torn's 5% sales tax, minus the shop price. "
            "Each scan also logs shop stock to `data/cityshop_stock.csv` so restocks can be learned later."
        )
        if st.button("Scan shops", key="scan_shops", disabled=not api_key):
            sess = session_for_requests()
            bucket = shared_bucket()
            with st.spinner("Reading city shop stock…"):
                items, err = fetch_city_shops(sess, api_key, bucket)
            if err:
                st.error(f"Couldn't read city shops: {err}")
                return
            stamp = pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M:%S")
            ids = sorted({it["item_id"] for it in items})
            bar = st.progress(0.0, text="Checking market prices…")

            def progress(done: int, total: int, row: dict) -> None:
                bar.progress(done / total, text=f"Checked {done}/{total}: {row.get('item_name') or row.get('item_id')}")

            start = time.monotonic()
            rows, _ = fetch_all_item_markets(sess, bucket, api_key, ids, {i: 0 for i in ids}, on_progress=progress)
            bar.empty()
            # Logged only once the scan completes, so a scan cut short by a page rerun isn't recorded twice.
            log_stock(paths.CITYSHOP_STOCK_PATH, items, stamp)
            st.session_state["shop_scan"] = {
                "df": scan_flips(items, rows),
                "when": pd.Timestamp.now().strftime("%H:%M"),
                "n_items": len(items),
                "secs": time.monotonic() - start,
            }
        scan = st.session_state.get("shop_scan")
        if not scan:
            return
        df = scan["df"]
        st.markdown(f"Scanned {scan['n_items']} shop items at {scan['when']} ({scan['secs']:.0f}s).")
        if df.empty:
            st.info("Nothing in the city shops resells at a profit right now.")
            return
        in_stock = df[df["In stock"] > 0]
        out = df[df["In stock"] == 0]
        cfg = {
            **{c: st.column_config.NumberColumn(format="compact") for c in ("Buy at", "Sell at", "Profit / unit", "Profit", "Cost")},
            **{c: st.column_config.NumberColumn(format="localized") for c in ("In stock", "Listed for sale", "Worth buying")},
        }
        if in_stock.empty:
            st.info("Every profitable item is sold out right now.")
        else:
            total = in_stock["Profit"].sum()
            st.markdown(
                f"**In stock and profitable** ({len(in_stock)}): about **{money(total)}** profit buying "
                f"{money(in_stock['Cost'].sum())} worth".replace("$", "\\$")
            )
            st.dataframe(
                in_stock, width="stretch", hide_index=True, column_config=cfg,
                column_order=("Item", "Shop", "Buy at", "Sell at", "Profit / unit", "Worth buying", "Profit", "Cost", "In stock"),
            )
        if not out.empty:
            st.markdown(f"**Profitable but sold out** ({len(out)}): check back after a restock.")
            st.dataframe(
                out, width="stretch", hide_index=True, column_config=cfg,
                column_order=("Item", "Shop", "Buy at", "Sell at", "Profit / unit", "Listed for sale"),
            )
        st.caption(
            "**Worth buying** is capped at how many are listed for sale now, on the idea that you can sell about "
            "that many without sinking the price; **Profit** and **Cost** are for that many. Buying is your click "
            "on the shop page."
        )
