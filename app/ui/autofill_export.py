"""Autofill script tab: generates and downloads the Tampermonkey userscript
that fills prices directly on Torn's Add Listing page.
"""

from __future__ import annotations

import json
import threading
from typing import Optional

import pandas as pd
import streamlit as st

from tma.autoupdate_server import DEFAULT_PORT, app_log, build_server
from tma.config import load_api_key, load_traders
from tma.http_client import BASE_URL, get_json_with_retry, session_for_requests, shared_bucket
from tma.item_stats import load_profiles
from tma.market_fetch import fetch_all_item_markets
from tma.prelanding import PreLanding, check_destination
from tma.shop_flips import fetch_city_shops, refresh_plan
from tma.traders import best_offers, refresh_prices
from tma.travel_plan import load_history
from tma.quality import PREMIUM_THRESHOLD, base_stats_map
from tma.userscript import TEMPLATE_HASH, build_userscript, template_of

from ..paths import (AUTOFILL_PATH, CITYSHOP_STOCK_PATH, FOREIGN_STOCK_PATH, LISTING_PLAN_PATH, FOREIGN_UPDATES_PATH, PROFILES_PATH, SHOP_BUYS_PATH,
                     SHOP_PLAN_PATH, STOCK_STATUS_PATH, TRADE_PLAN_PATH, TRADER_PRICES_PATH, TRAVEL_PRICES_PATH,
                     TRIP_BUYS_PATH, USERSCRIPT_INPUTS_PATH)
from .stock_minimums import status_json


def _prelanding() -> PreLanding:
    """Checks the destination's prices a few minutes before you land abroad
    (the userscript reports the flight); results go to the script log."""

    def lookup():
        key = load_api_key()
        if not key:
            return None, "no API key"
        return get_json_with_retry(session_for_requests(), shared_bucket(), f"{BASE_URL}/user/travel", key,
                                   timeout=15, retries=1)

    def check(code: str) -> str:
        session, key = session_for_requests(), load_api_key()
        refs = {int(k): v.get("market_price") or 0 for k, v in load_profiles(PROFILES_PATH).items()}
        traders = load_traders()
        offers = best_offers(traders, refresh_prices(TRADER_PRICES_PATH, traders, session)[0])

        def fetch(ids: list[int]) -> dict[int, dict]:
            return fetch_all_item_markets(session, shared_bucket(), key, ids, {i: 0 for i in ids}, deadline_s=90)[0]

        h = load_history(FOREIGN_STOCK_PATH, FOREIGN_UPDATES_PATH)
        return check_destination(code, h, refs, offers, fetch, TRAVEL_PRICES_PATH)

    return PreLanding(lookup, check, lambda msg: app_log(AUTOFILL_PATH, msg))


_shop_lock = threading.Lock()


def _refresh_shop_plan() -> None:
    """The userscript found nothing to buy on a stale plan: scan the city
    shops and save a new one (one scan at a time; results go to the script log)."""
    if not _shop_lock.acquire(blocking=False):
        return
    try:
        session, key = session_for_requests(), load_api_key()
        if not key:
            app_log(AUTOFILL_PATH, "shop plan: no API key")
            return
        refs = {int(k): v.get("market_price") or 0 for k, v in load_profiles(PROFILES_PATH).items()}

        def fetch(ids: list[int]) -> dict[int, dict]:
            return fetch_all_item_markets(session, shared_bucket(), key, ids, {i: 0 for i in ids}, deadline_s=150)[0]

        line = refresh_plan(lambda: fetch_city_shops(session, key, shared_bucket()), fetch, refs, SHOP_PLAN_PATH,
                            CITYSHOP_STOCK_PATH)
        app_log(AUTOFILL_PATH, "shop plan: " + line)
    except Exception as exc:  # a background thread: say what broke instead of dying silently
        app_log(AUTOFILL_PATH, f"shop plan: refresh failed: {type(exc).__name__}: {exc}")
    finally:
        _shop_lock.release()


@st.cache_resource
def _ensure_update_server(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """Start the Tampermonkey update server on a background thread, once per app process.

    st.cache_resource guarantees this body runs at most once for the life of the
    Streamlit process, so repeated script reruns never try to rebind the port.
    """
    url = f"http://127.0.0.1:{port}/{AUTOFILL_PATH.name}"
    try:
        server = build_server(AUTOFILL_PATH, port=port, reports_path=SHOP_BUYS_PATH, status_path=STOCK_STATUS_PATH,
                              travel_path=TRAVEL_PRICES_PATH, plan_path=SHOP_PLAN_PATH,
                              trips_path=TRIP_BUYS_PATH, foreign_path=FOREIGN_STOCK_PATH, trade_path=TRADE_PLAN_PATH,
                              on_flying=_prelanding().flying, on_shop_refresh=_refresh_shop_plan,
                              listing_path=LISTING_PLAN_PATH)
    except OSError:
        # Port already bound — most likely this same server from a prior process
        # (e.g. `tools/update_server.py` run by hand), so the URL is still live.
        return True, url
    threading.Thread(target=server.serve_forever, daemon=True, name="tma-autoupdate-server").start()
    return True, url


def write_userscript(price_map: dict, base_stats: dict, cfg: dict, shop_run) -> tuple[str, Optional[str]]:
    """Builds and writes the script, and saves what it was built from, so a
    code update can rebuild it (refresh_userscript) without waiting for the
    next Run. Returns (script, error writing it or None)."""
    userjs = build_userscript(price_map, base_stats, cfg=cfg, shop_run=shop_run, stock=json.loads(status_json()))
    try:
        AUTOFILL_PATH.write_text(userjs, encoding="utf-8")
    except OSError as exc:
        return userjs, str(exc)
    try:
        USERSCRIPT_INPUTS_PATH.write_text(json.dumps({
            "price_map": {str(k): v for k, v in price_map.items()},
            "base_stats": {str(k): v for k, v in base_stats.items()}, "cfg": cfg, "shop_run": shop_run,
        }), encoding="utf-8")
    except OSError:
        pass
    return userjs, None


def refresh_userscript() -> str:
    """Rebuilds the served script when it was built from older code.
    Returns "rebuilt", "current", "needs-run" (stale, but no saved inputs:
    built before this existed) or "none" (never built)."""
    try:
        built = AUTOFILL_PATH.read_text(encoding="utf-8")
    except OSError:
        return "none"
    if template_of(built) == TEMPLATE_HASH:
        return "current"
    try:
        inputs = json.loads(USERSCRIPT_INPUTS_PATH.read_text(encoding="utf-8"))
        _, error = write_userscript(inputs["price_map"], inputs["base_stats"], inputs["cfg"], inputs["shop_run"])
    except (OSError, ValueError, KeyError, TypeError):
        return "needs-run"
    return "needs-run" if error else "rebuilt"


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

    userjs, error = write_userscript(
        price_map,
        base_stats_map(profiles),
        {"dumpUndercutPct": rules.dump_undercut_pct, "topMinQuality": rules.top_min_quality,
         "keepCash": rules.keep_cash, "ipecacOver": rules.ipecac_over},
        plan_ctx.get("shop_run"),
    )
    write_ok = error is None
    if error:
        st.caption(f"Could not write update copy: {error}")

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
