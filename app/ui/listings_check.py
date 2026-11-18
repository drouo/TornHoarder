"""Listings: what you already have on the Item Market, checked against
everyone else's listings and re-priced from your listings page."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from tma.http_client import session_for_requests, shared_bucket
from tma.item_stats import load_profiles
from tma.market_fetch import fetch_all_item_markets
from tma.my_listings import (BIG_DROP_PCT, REF_WARN, fetch_my_listings, market_listings, review_listing,
                             without_mine, write_listing_plan)
from tma.shops import load_shop_knowledge, shop_price_for

from .. import paths
from .page_progress import PageProgress
from .sell_plan import VIEW_LISTINGS_URL


def check_listings(api_key: str) -> tuple[list[dict], list[str]]:
    """(review rows, errors): your listings, each against the other sellers."""
    sess, bucket = session_for_requests(), shared_bucket()
    listed, err = fetch_my_listings(sess, api_key, bucket)
    if err:
        return [], [f"Couldn't read your listings: {err}"]
    ids = sorted({int(l["item"]["id"]) for l in listed if (l.get("item") or {}).get("id")})
    page = PageProgress()
    bar = st.progress(0.0, text="Checking the market…")

    def progress(done: int, total: int, row: dict) -> None:
        bar.progress(done / total, text=f"Checked {done}/{total}: {row.get('item_name') or row.get('item_id')}")
        page.update(done / total, f"Checking your listings {done}/{total}")

    rows, _ = fetch_all_item_markets(sess, bucket, api_key, ids, {i: 0 for i in ids}, on_progress=progress)
    bar.empty()
    page.clear()
    errors = [f"{iid}: {r['error']}" for iid, r in rows.items() if "error" in r]
    profiles = load_profiles(paths.PROFILES_PATH)
    known = load_shop_knowledge(paths.SHOP_BUYS_PATH)
    out = []
    for l in listed:
        iid = int((l.get("item") or {}).get("id") or 0)
        row = rows.get(iid)
        if not iid or not row or "error" in row:
            continue
        prof = profiles.get(str(iid))
        out.append(review_listing(l, market_listings(without_mine(row, listed)), shop_price_for(prof, iid, known),
                                  (prof or {}).get("market_price")))
    return out, errors


def render_listings_check(api_key: str) -> None:
    st.subheader("Your listings")
    with st.container(border=True):
        st.caption(
            "Reads what you have listed on the Item Market and checks each listing against everyone else's (yours are "
            "never counted, so you can't undercut yourself). About one API call per item you have listed."
        )
        if st.button("Check my listings", key="check_listings", disabled=not api_key):
            start = time.monotonic()
            rows, errors = check_listings(api_key)
            for e in errors:
                st.caption(e)
            write_listing_plan(paths.LISTING_PLAN_PATH, rows)
            st.session_state["listings_check"] = {"rows": rows, "when": pd.Timestamp.now().strftime("%H:%M"),
                                                  "secs": time.monotonic() - start}
        got = st.session_state.get("listings_check")
        if not got:
            return
        df = pd.DataFrame(got["rows"])
        if df.empty:
            st.info("No active Item Market listings.")
            return
        fix = df[df["New price"].notna()]
        big = fix[fix["Big drop"]]
        st.markdown(
            f"Checked {len(df)} listing(s) at {got['when']} ({got['secs']:.0f}s): **{len(fix)} worth re-pricing**"
            + (f", **⚠ {len(big)} by a lot**" if len(big) else "") + "."
        )
        df = df.assign(**{"⚠": df["Big drop"].map(lambda b: "⚠" if b else "")})
        st.dataframe(
            df.sort_values(["Big drop", "New price", "Item"], ascending=[False, True, True], na_position="last"),
            width="stretch", hide_index=True,
            column_order=("⚠", "Item", "Quality", "Amount", "Your price", "New price", "Status", "Why"),
            column_config={
                "Amount": st.column_config.NumberColumn(format="localized"),
                **{c: st.column_config.NumberColumn(format="compact") for c in ("Your price", "New price")},
            },
        )
        st.markdown(f"[**Open your listings to re-price →**]({VIEW_LISTINGS_URL})")
        st.caption(
            "On that page the Torn script lists these re-prices: it fills the new prices into Torn's boxes and you press "
            f"Torn's **Save changes**. ⚠ marks a cut of more than {BIG_DROP_PCT:.0f}% or below {REF_WARN:.0%} of "
            "Torn's market value: those are left out until you tick them. **Why** says what each price rests on; "
            "listings far under the rest (dumpers) are ignored, and no price goes under what a shop pays or half "
            "Torn's market value."
        )
