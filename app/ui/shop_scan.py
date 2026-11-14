"""Scan shops: city-shop items in stock now that resell at a profit."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from tma.fmt import money
from tma.item_stats import load_profiles
from tma.http_client import session_for_requests, shared_bucket
from tma.market_fetch import fetch_all_item_markets
from tma.shop_flips import (
    DAILY_CAP, daily_buy_plan, day_start, fetch_city_shops, log_stock, plan_route, reset_in_s, scan_flips, write_plan,
)

from .. import paths
from .page_progress import PageProgress


def render_shop_scan(api_key: str) -> None:
    with st.expander("Shop flips: buy in city shops, resell on the market"):
        st.caption(
            "Checks every city shop's current stock against live market listings (about 110 API calls, a minute "
            "or so). Profit is $1 under the cheapest listing (never above Torn's market value, what "
            "it has actually been selling for) after Torn's 5% sales tax, minus the shop price. "
            "Each scan also logs shop stock to `data/cityshop_stock.csv` so restocks can be learned later."
        )
        left = reset_in_s()
        st.markdown(
            f"🛒 The 100-a-day limit resets at midnight Torn time, in **{left // 3600}h {left % 3600 // 60:02d}m**. "
            "The badge on Torn pages counts what you've bought today."
        )
        if st.button("Scan shops", key="scan_shops", disabled=not api_key):
            sess = session_for_requests()
            bucket = shared_bucket()
            page = PageProgress()
            page.update(0.01, "Reading city shop stock…")
            items, err = fetch_city_shops(sess, api_key, bucket)
            if err:
                page.clear()
                st.error(f"Couldn't read city shops: {err}")
                return
            stamp = pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M:%S")
            ids = sorted({it["item_id"] for it in items})
            bar = st.progress(0.0, text="Checking market prices…")

            def progress(done: int, total: int, row: dict) -> None:
                bar.progress(done / total, text=f"Checked {done}/{total}: {row.get('item_name') or row.get('item_id')}")
                page.update(0.02 + 0.98 * done / total, f"Checking shop prices {done}/{total} · {row.get('item_name') or row.get('item_id')}")

            start = time.monotonic()
            rows, _ = fetch_all_item_markets(sess, bucket, api_key, ids, {i: 0 for i in ids}, on_progress=progress)
            bar.empty()
            page.clear()
            # Logged only once the scan completes, so a scan cut short by a page rerun isn't recorded twice.
            log_stock(paths.CITYSHOP_STOCK_PATH, items, stamp)
            st.session_state["shop_scan"] = {
                "df": scan_flips(items, rows, {int(k): v.get("market_price") for k, v in load_profiles(paths.PROFILES_PATH).items()}),
                "when": pd.Timestamp.now().strftime("%H:%M"),
                "n_items": len(items),
                "secs": time.monotonic() - start,
            }
        scan = st.session_state.get("shop_scan")
        if not scan:
            return
        min_margin = st.number_input(
            "Minimum margin %", min_value=0.0, max_value=10000.0, value=10.0, step=5.0, key="min_margin",
            help="Leave out flips that earn less than this share of what you pay, e.g. $160k on a $45m car is 0.35%.",
        )
        df = scan["df"]
        if not df.empty:
            df = df[df["Margin %"] >= min_margin]
        st.markdown(f"Scanned {scan['n_items']} shop items at {scan['when']} ({scan['secs']:.0f}s).")
        if df.empty:
            st.info("Nothing in the city shops resells at a profit right now.")
            return
        in_stock = df[df["In stock"] > 0]
        out = df[df["In stock"] == 0]
        cfg = {
            **{c: st.column_config.NumberColumn(format="compact") for c in ("Buy at", "Sell at", "Market value", "Profit / unit", "Profit", "Cost")},
            "Margin %": st.column_config.NumberColumn(format="%.0f%%"),
            **{c: st.column_config.NumberColumn(format="localized") for c in ("In stock", "Listed for sale", "Worth buying")},
        }
        cap = st.number_input(
            "Daily shop purchase limit", min_value=1, max_value=1000, value=DAILY_CAP, step=10, key="daily_buy_cap",
            help="Torn lets you buy 100 items a day from city shops, all shops together.",
        )
        buys, spare = daily_buy_plan(df, int(cap))
        if not buys.empty:
            st.markdown(
                f"**Today's {int(buys['Buy'].sum())} buys**: about **{money(buys['Profit'].sum())}** profit for "
                f"{money(buys['Cost'].sum())}. Most profit per item first, since the limit counts items, not dollars."
                .replace("$", "\\$")
            )
            plan_cfg = {**cfg, "Buy": st.column_config.NumberColumn(format="localized")}
            plan_cols = ("Item", "Shop", "Buy", "Buy at", "Sell at", "Profit / unit", "Margin %", "Profit", "Cost", "In stock")
            st.dataframe(buys, width="stretch", hide_index=True, column_config=plan_cfg, column_order=plan_cols)
            route = plan_route(buys, day_start())
            write_plan(paths.SHOP_PLAN_PATH, route)
            st.markdown("  \n".join(f"[**{s['label']} →**]({s['url']})" for s in route).replace("$", "\\$"))
            st.caption(
                "Each link opens the shop with the userscript ready to fill the amounts; you click Buy and confirm. "
                "When the limit resets, the badge on Torn pages offers this plan as one run through every shop."
            )
            if not spare.empty:
                st.markdown("**Backups**, if a pick has sold out when you get there:")
                st.dataframe(spare, width="stretch", hide_index=True, column_config=plan_cfg, column_order=plan_cols)
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
                column_order=("Item", "Shop", "Buy at", "Sell at", "Profit / unit", "Margin %", "Worth buying", "Profit", "Cost", "In stock"),
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
