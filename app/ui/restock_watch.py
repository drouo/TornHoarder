"""Restock watch: checks city shop stock once a minute while the page is open
and calls out when a high-value item comes back, since the best ones (Beer,
Cagoutte, ...) sell out within minutes of a restock."""

from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from tma.fmt import money
from tma.http_client import session_for_requests, shared_bucket
from tma.item_stats import load_profiles
from tma.shop_flips import fetch_city_shops, log_stock, restock_times, shop_values, stock_changes
from tma.shops import OWN_PAGE_URLS, SHOP_URL, VENDOR_STEPS

from .. import paths

# A short two-tone chime; browsers may block it until you've clicked the page.
_BEEP = """<script>
try {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  [880, 1320].forEach((f, i) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.2, ctx.currentTime + i * 0.25);
    o.start(ctx.currentTime + i * 0.25); o.stop(ctx.currentTime + i * 0.25 + 0.2);
  });
} catch (e) {}
</script>"""


# torn/cityshops names a few shops differently from the shop list in tma.shops.
_ALIASES = {"Print Store": "Print Shop"}


def shop_url(shop: str) -> str | None:
    step = VENDOR_STEPS.get(_ALIASES.get(shop, shop))
    return (OWN_PAGE_URLS.get(step) or SHOP_URL.format(step=step)) if step else None


def render_restock_watch(api_key: str) -> None:
    with st.expander("Restock watch: catch the best shop items when they come back", expanded=bool(st.session_state.get("watch_on"))):
        st.caption(
            "The best city-shop flips are almost always sold out: players buy them within minutes of a restock. "
            "While this is on, the app checks shop stock once a minute (1 API call) and chimes when a watched item "
            "comes back. Profit is at Torn's market value after the 5% tax. Every change is logged, so the restock "
            "times below fill in over a day or two."
        )
        c1, c2 = st.columns(2)
        min_profit = c1.number_input("Watch items worth at least (profit each)", min_value=0, value=500, step=100, key="watch_min_profit")
        min_margin = c2.number_input("…with a margin of at least %", min_value=0.0, value=10.0, step=5.0, key="watch_min_margin")
        if st.toggle("Watch shop stock", key="watch_on", disabled=not api_key):
            _watch(api_key, min_profit, min_margin)
        else:
            _restock_history(min_profit, min_margin, st.session_state.get("watch_values"))


@st.fragment(run_every=60)
def _watch(api_key: str, min_profit: float, min_margin: float) -> None:
    items, err = fetch_city_shops(session_for_requests(), api_key, shared_bucket())
    if err:
        st.warning(f"Couldn't read shop stock: {err}. Trying again in a minute.")
        return
    prev = st.session_state.get("watch_prev") or {}
    changed, restocked = stock_changes(prev, items)
    stamp = pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M:%S")
    log_stock(paths.CITYSHOP_STOCK_PATH, changed, stamp)
    st.session_state["watch_prev"] = {it["item_id"]: it["stock"] for it in items}

    ref = {int(k): v.get("market_price") for k, v in load_profiles(paths.PROFILES_PATH).items()}
    values = shop_values(items, ref)
    st.session_state["watch_values"] = values
    watched = _watched(values, min_profit, min_margin)
    hits = [it for it in restocked if it["item_id"] in set(watched["item_id"])]
    alerts = st.session_state.setdefault("watch_alerts", [])
    for it in hits:
        row = watched[watched["item_id"] == it["item_id"]].iloc[0]
        msg = f"{it['item_name']}: {it['stock']:,} in stock at {it['shop']} · {money(row['Profit / unit'])} profit each"
        alerts.insert(0, {"time": stamp[11:16], "msg": msg, "url": shop_url(it["shop"])})
        st.toast("🔔 " + msg)
    del alerts[20:]
    if hits:
        components.html(_BEEP, height=0)

    now_in = watched[watched["In stock"] > 0]
    st.markdown(f"Checked at **{stamp[11:19]}** · watching {len(watched)} item(s) · **{len(now_in)} in stock now**")
    for a in alerts[:5]:
        link = f" · [Buy →]({a['url']})" if a["url"] else ""
        st.markdown(f"🔔 **{a['time']}** {a['msg']}{link}".replace("$", "\\$"))
    _table(watched)
    _restock_history(min_profit, min_margin, values)


def _watched(values: pd.DataFrame, min_profit: float, min_margin: float) -> pd.DataFrame:
    if values is None or values.empty:
        return pd.DataFrame(columns=["item_id", "In stock", "Profit / unit"])
    return values[(values["Profit / unit"] >= min_profit) & (values["Margin %"] >= min_margin)]


def _table(watched: pd.DataFrame) -> None:
    if watched.empty:
        st.info("No city-shop item clears those thresholds.")
        return
    df = watched.copy()
    df["Shop page"] = df["Shop"].map(shop_url)
    st.dataframe(
        df, width="stretch", hide_index=True,
        column_order=("Item", "Shop", "In stock", "Buy at", "Market value", "Profit / unit", "Margin %", "Shop page"),
        column_config={
            **{c: st.column_config.NumberColumn(format="compact") for c in ("Buy at", "Market value", "Profit / unit")},
            "Margin %": st.column_config.NumberColumn(format="%.0f%%"),
            "In stock": st.column_config.NumberColumn(format="localized"),
            "Shop page": st.column_config.LinkColumn(display_text="Open →"),
        },
    )


def _restock_history(min_profit: float, min_margin: float, values) -> None:
    try:
        log = pd.read_csv(paths.CITYSHOP_STOCK_PATH)
    except (OSError, ValueError):
        return
    watched = _watched(values, min_profit, min_margin) if values is not None else None
    ids = watched["item_id"] if watched is not None and not watched.empty else log["item_id"].unique()
    rises = restock_times(log, ids)
    if rises.empty:
        st.caption("No restocks logged yet for the watched items.")
        return
    st.markdown(f"**Restocks seen** ({len(rises)}), newest first:")
    st.dataframe(
        rises.iloc[::-1].head(50), width="stretch", hide_index=True,
        column_config={"time": "When", "item_name": "Item", "shop": "Shop",
                       "from": st.column_config.NumberColumn("From", format="localized"),
                       "stock": st.column_config.NumberColumn("To", format="localized")},
    )
