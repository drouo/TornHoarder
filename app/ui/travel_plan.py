"""Where to fly: destinations ranked by expected profit per hour, from the
foreign stock history (tools/log_foreign_stock.py) and live market prices."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from tma.fmt import money
from tma.http_client import session_for_requests, shared_bucket
from tma.item_stats import load_profiles
from tma.market_fetch import fetch_all_item_markets
from tma.travel_plan import TRAVEL_SPEED, candidates, load_history, plan_trips, sell_prices

from .. import paths
from .page_progress import PageProgress


def render_travel_plan(api_key: str) -> None:
    with st.expander("Where to fly"):
        h = load_history(paths.FOREIGN_STOCK_PATH, paths.FOREIGN_UPDATES_PATH)
        if not h.obs:
            st.info("No foreign stock history yet. Start the logger: `python tools/log_foreign_stock.py`.")
            return
        st.caption(
            f"Odds come from {h.span_h:.0f} hours of logged stock abroad: for each item, every past moment it was "
            "in the same state as now (sold out, short of a full load, or a full load), and what was there one "
            "flight later. Profit is $1 under the cheapest listing (never above market value) after the 5% tax, "
            "minus the cost abroad. More history means better odds; a few days covers every time of day."
        )
        c1, c2 = st.columns(2)
        cap = c1.number_input("Items you can carry", min_value=1, max_value=100, value=18, key="travel_cap")
        kind = c2.selectbox("Travel by", list(TRAVEL_SPEED), index=list(TRAVEL_SPEED).index("Private jet"), key="travel_kind")
        if st.button("Plan a trip", key="plan_trip", disabled=not api_key):
            refs = {int(k): v.get("market_price") or 0 for k, v in load_profiles(paths.PROFILES_PATH).items()}
            ids = candidates(h, refs)
            page = PageProgress()
            bar = st.progress(0.0, text="Checking market prices…")

            def progress(done: int, total: int, row: dict) -> None:
                bar.progress(done / total, text=f"Checked {done}/{total}: {row.get('item_name') or row.get('item_id')}")
                page.update(done / total, f"Checking prices for items abroad {done}/{total}")

            rows, _ = fetch_all_item_markets(session_for_requests(), shared_bucket(), api_key, ids, {i: 0 for i in ids}, on_progress=progress)
            bar.empty()
            page.clear()
            st.session_state["travel_plan"] = {"sell": sell_prices(rows, refs), "when": pd.Timestamp.now().strftime("%H:%M")}
        got = st.session_state.get("travel_plan")
        if not got:
            return
        # Odds re-run on every rerun against the latest log; prices are from the last check.
        df = plan_trips(h, time.time(), got["sell"], cap=int(cap), speed=TRAVEL_SPEED[kind])
        if df.empty:
            st.info("Nothing abroad resells at a profit right now.")
            return
        top = df.iloc[0]
        st.markdown(
            f"**Fly to {top['Country']}** for {top['Best item']}: about **{money(top['Expected trip profit'])}** "
            f"a trip, **{money(top['Per hour'])}/hour** ({top['Chance of a full load']}% chance all {int(cap)} are "
            f"still there when you land). Prices checked at {got['when']}.".replace("$", "\\$")
        )
        st.dataframe(
            df, width="stretch", hide_index=True,
            column_order=("Country", "Per hour", "Expected trip profit", "Best item", "Profit / unit", "In stock now",
                          "Chance of a full load", "Flight (min)", "Backups", "Odds from", "Data age (min)"),
            column_config={
                **{c: st.column_config.NumberColumn(format="compact") for c in ("Per hour", "Expected trip profit", "Profit / unit")},
                "Chance of a full load": st.column_config.NumberColumn(format="%d%%"),
                "In stock now": st.column_config.NumberColumn(format="localized"),
            },
        )
        st.caption(
            "**Expected trip profit** assumes you buy the best item and top up with the backups if it's run short. "
            "**Per hour** divides by the round trip. **Odds from** says how many past moments back the odds: "
            "\"like now\" matched today's state, \"any time\" means too few matches so far, so it uses every moment."
        )
