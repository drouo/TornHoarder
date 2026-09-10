import sys
import time
import threading
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tma.config import load_api_key, save_api_key, clear_api_key
from tma.inventory import (
    fetch_inventory,
    fetch_item_stats,
    fetch_item_stats_many,
    to_rows,
    InventoryEntry,
    INVENTORY_CATEGORIES,
    CATEGORY_LABELS,
)
from tma.inventory_matcher import load_dictionary, match_inventory
from tma.market_enrichment import (
    wide_to_long,
    enrich_all_items,
    build_summary_from_enriched,
    price_at_quality,
    quality_from_stats,
)
from tma.item_stats import StatValueModel, load_profiles, sidecar, stat_warnings
from tma.quality import base_stats_map, PREMIUM_THRESHOLD
from tma.userscript import build_userscript

BASE_URL = "https://api.torn.com/v2"
MAX_WORKERS = 5
RATE_LIMIT_PER_MIN = 90
RETRIES = 3
TIMEOUT = 15
MARKET_PHASE_TIMEOUT_S = 1200.0

DICT_PATH = (ROOT_DIR / "data" / "torn_item_dictionary.csv").resolve()
PROFILES_PATH = (ROOT_DIR / "data" / "torn_item_profiles.json").resolve()


class TokenBucket:
    def __init__(self, rate_per_min: int, capacity: int | None = None) -> None:
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = capacity or rate_per_min
        self.tokens = float(self.capacity)
        self.last = time.perf_counter()
        self.lock = threading.Lock()

    def take(self, tokens: int = 1) -> None:
        while True:
            with self.lock:
                now = time.perf_counter()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate_per_sec)
                self.last = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
            time.sleep(0.01)


def session_for_requests() -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=MAX_WORKERS * 10,
        pool_maxsize=MAX_WORKERS * 10,
    )
    session.mount("https://", adapter)
    session.headers.update(
        {
            "accept": "application/json",
            "User-Agent": "torn-itemmarket-web/1.1",
        }
    )
    return session


def attempt_call(
    session: requests.Session,
    bucket: TokenBucket,
    api_key: str,
    item_id: int,
    mode: int,
) -> tuple[int | None, dict]:
    url = f"{BASE_URL}/market/{item_id}/itemmarket"
    headers: dict[str, str] = {}
    params: dict[str, int | str] = {"limit": 100, "offset": 0}

    if mode == 1:
        headers["Authorization"] = f"Apikey {api_key}"
    elif mode == 2:
        headers["Authorization"] = f"ApiKey {api_key}"
    else:
        params["key"] = api_key

    bucket.take(1)
    resp = session.get(url, headers=headers, params=params, timeout=TIMEOUT)
    return resp.status_code, resp.json()


def _cooldown_seconds(msg) -> float:
    import re as _re

    if not msg:
        return 15.0
    m = _re.search(r"resets?\s+at\s+(\d{1,2}):(\d{2}):(\d{2})", str(msg), _re.IGNORECASE)
    if not m:
        return 15.0
    hh, mm, ss = (int(g) for g in m.groups())
    return float(min(hh * 3600 + mm * 60 + ss + 1, 60))


def fetch_100(
    session: requests.Session,
    bucket: TokenBucket,
    api_key: str,
    item_id: int,
    my_quantity: int,
) -> dict:
    backoff = 0.8

    for _ in range(1, RETRIES + 1):
        for mode in (1, 2, 3):
            try:
                status, data = attempt_call(session, bucket, api_key, item_id, mode)
            except Exception as exc:
                status, data = None, {"error": {"code": -1, "error": str(exc)}}

            if isinstance(data, dict) and "error" in data:
                code = data["error"].get("code")
                msg = data["error"].get("error")

                if code == 2 and mode != 3:
                    continue

                if status == 429 or (code == 0 and "too many" in (msg or "").lower()):
                    time.sleep(_cooldown_seconds(msg))
                    break

                if code in (0, 10) or status in (500, 502, 503, 504):
                    time.sleep(backoff)
                    backoff *= 1.6
                    break

                return {
                    "item_id": item_id,
                    "my_quantity": my_quantity,
                    "error": f"API error {code}: {msg}",
                }

            itemmarket = (data or {}).get("itemmarket", {}) or {}
            item = itemmarket.get("item", {}) or {}
            listings = itemmarket.get("listings", []) or []

            n = min(len(listings), 100)

            row: dict[str, object] = {
                "item_id": item.get("id", item_id),
                "item_name": item.get("name"),
                "item_type": item.get("type"),
                "average_price": item.get("average_price"),
                "my_quantity": my_quantity,
            }

            for i, listing in enumerate(listings[:n], start=1):
                row[f"price_{i}"] = listing.get("price")
                row[f"amount_{i}"] = listing.get("amount")
                details = listing.get("item_details") or {}
                stats = details.get("stats") or {}
                row[f"quality_{i}"] = stats.get("quality")
                row[f"damage_{i}"] = stats.get("damage")
                row[f"accuracy_{i}"] = stats.get("accuracy")
                row[f"armor_{i}"] = stats.get("armor")

            for i in range(n + 1, 101):
                row[f"price_{i}"] = None
                row[f"amount_{i}"] = None
                row[f"quality_{i}"] = None
                row[f"damage_{i}"] = None
                row[f"accuracy_{i}"] = None
                row[f"armor_{i}"] = None

            return row

        time.sleep(backoff)
        backoff *= 1.6

    return {"item_id": item_id, "my_quantity": my_quantity, "error": "Exhausted retries"}


def fmt_int(x) -> str:
    if pd.isna(x):
        return ""
    try:
        return f"{int(x):,}"
    except Exception:
        return str(x)


@st.cache_data
def load_stat_layer() -> tuple[dict, StatValueModel]:
    profiles = load_profiles(PROFILES_PATH)
    model = StatValueModel()
    model.fit(profiles)
    return profiles, model


ADD_LISTING_URL = "https://www.torn.com/page.php?sid=ItemMarket#/addListing"

TIER_COLORS = {
    "Common": "#e3e3e3",
    "Solid": "#c8e6c9",
    "Premium": "#bbdefb",
    "Rare": "#e1bee7",
    "Elite": "#ffd97a",
}

LIST_BASIS_OPTIONS = ("Fast-sell price", "Fair price", "Greedy price")


def tier_style(v):
    color = TIER_COLORS.get(v)
    if not color:
        return ""
    return f"background-color: {color}; font-weight: 600;"


def render_results(an: dict) -> None:
    df_summary = an["df_summary"]
    warnings = an["warnings"]

    st.subheader("Price overview")

    elevated = [w for w in warnings if w["elevated"]]
    moderate = [w for w in warnings if not w["elevated"]]

    def _banner_lines(items):
        return "\n".join(
            f"- **{w['item_name']}** (x{fmt_int(w['my_quantity'])}): "
            f"order-book median {fmt_int(w['fair_price'])} vs Torn reference "
            f"{fmt_int(w['market_price'])} ({w['ratio']:.1f}-times above)"
            for w in items
        )

    if elevated:
        st.error(
            "HIGH-VALUE stat items priced well below market reference — verify before listing:\n"
            + _banner_lines(elevated)
        )
    if moderate:
        st.warning("Some items are priced below Torn's market reference:\n" + _banner_lines(moderate))

    strategy = st.radio(
        "List price basis",
        LIST_BASIS_OPTIONS,
        index=0,
        horizontal=True,
        key="list_strategy",
        help="Weapon/armor copies are priced on the market's own quality curve from each copy's stats; other items use this basis for the pre-filled price.",
    )

    strategy_key = {"Fast-sell price": "fast", "Fair price": "fair", "Greedy price": "greedy"}[strategy]
    strategy_col = {"Fast-sell price": "fast_sell_price", "Fair price": "fair_price", "Greedy price": "greedy_price"}[strategy]

    auto_stat = st.checkbox(
        "Price weapon/armor copies from their stats",
        value=True,
        key="auto_stat_pricing",
        help="Each copy's Damage/Accuracy (weapons) or Armor value is mapped onto the market's price-vs-quality curve. "
        "Uncheck to use the typed 'List price' values verbatim.",
    )

    entries = an.get("entries")
    uid_stats = an.get("uid_stats") or {}
    payload_by_item: dict[int, dict] = {}
    for _, r in df_summary.iterrows():
        ptool = r.get("copy_tool")
        if isinstance(ptool, dict):
            payload_by_item[int(r["item_id"])] = ptool

    def _stat_suggestion(payload, quality):
        cur = price_at_quality(payload, quality)
        if not cur["derived"]:
            return None, None, None, None
        return cur["fast"], cur["fair"], cur["greedy"], cur

    def _copy_stats(payload, copy):
        median = payload.get("median_stats") or {}
        real = uid_stats.get(str(copy.uid), {}) if copy and copy.uid else {}
        if "quality" not in (real or {}):
            real = {}
        stats = {
            "damage": real.get("damage") if real.get("damage") is not None else median.get("damage"),
            "accuracy": real.get("accuracy") if real.get("accuracy") is not None else median.get("accuracy"),
            "armor": real.get("armor") if real.get("armor") is not None else median.get("armor"),
        }
        is_weapon = payload.get("stat_type") == "Weapon"
        return {
            "stats": {
                "damage": stats["damage"] if is_weapon else None,
                "accuracy": stats["accuracy"] if is_weapon else None,
                "armor": stats["armor"] if not is_weapon else None,
            },
            "quality": real.get("quality") if real.get("quality") is not None else None,
        }

    rows_out: list[dict] = []
    real_q_by_idx: dict[int, float] = {}
    for _, r in df_summary.iterrows():
        iid = int(r["item_id"])
        payload = payload_by_item.get(iid) or {}
        stat_type = payload.get("stat_type")
        market_link = "https://www.torn.com/page.php?sid=ItemMarket#/addListing"
        points = payload.get("points") or []

        copies = [e for e in (entries or []) if e.item_id == iid and not e.equipped]
        can_price_by_stats = payload.get("stat_type") in ("Weapon", "Armor") and bool(points)

        if can_price_by_stats and (copies or entries is None):
            med_q = payload.get("median_quality") or 0.0
            is_weapon = stat_type == "Weapon"
            target = copies if copies else [None]
            for copy in target:
                cinfo = _copy_stats(payload, copy)
                cstats = cinfo["stats"]
                quality = float(cinfo["quality"] or med_q)
                real_q_by_idx[len(rows_out)] = cinfo["quality"]
                fast, fair, greedy, cur = _stat_suggestion(payload, quality)
                list_price = int(round(cur[strategy_key])) if cur else int(round(r[strategy_col] or 0))
                rows_out.append(
                    {
                        "item_id": iid,
                        "Item": r["item_name"],
                        "Tier": r["stat_tier"],
                        "Market": market_link,
                        "My quantity": 1,
                        "List qty": 1,
                        "List price": list_price,
                        "Fast-sell price": int(round(fast)) if fast else None,
                        "Fair price": int(round(fair)) if fair else None,
                        "Greedy price": int(round(greedy)) if greedy else None,
                        "Torn ref": r.get("market_price_ref"),
                        "Damage %": cstats["damage"],
                        "Accuracy %": cstats["accuracy"],
                        "Armor": cstats["armor"],
                        "quality": quality,
                        "is_stat": True,
                        "payload": payload,
                    }
                )
        else:
            rows_out.append(
                {
                    "item_id": iid,
                    "Item": r["item_name"],
                    "Tier": r["stat_tier"],
                    "Market": market_link,
                    "My quantity": r["my_quantity"],
                    "List qty": r["my_quantity"],
                    "List price": int(round(r[strategy_col] or 0)),
                    "Fast-sell price": r["fast_sell_price"],
                    "Fair price": r["fair_price"],
                    "Greedy price": r["greedy_price"],
                    "Torn ref": r.get("market_price_ref"),
                    "Damage %": None,
                    "Accuracy %": None,
                    "Armor": None,
                    "quality": None,
                    "is_stat": False,
                    "payload": payload,
                }
            )

    display_cols = [
        "Item",
        "Tier",
        "Market",
        "My quantity",
        "List qty",
        "List price",
        "Fast-sell price",
        "Fair price",
        "Greedy price",
        "Torn ref",
        "Damage %",
        "Accuracy %",
        "Armor",
        "item_id",
    ]
    base = pd.DataFrame(rows_out)[display_cols]

    column_config = {
        "Item": st.column_config.Column(disabled=True),
        "Tier": st.column_config.Column(disabled=True),
        "Market": st.column_config.LinkColumn(display_text="Sell"),
        "My quantity": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        "List price": st.column_config.NumberColumn(min_value=0, step=1),
        "List qty": st.column_config.NumberColumn(min_value=1, step=1),
        "Fast-sell price": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        "Fair price": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        "Greedy price": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        "Torn ref": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        "Damage %": st.column_config.NumberColumn(min_value=0.0, step=0.01, help="Your copy's damage stat (from its tooltip)."),
        "Accuracy %": st.column_config.NumberColumn(min_value=0.0, step=0.01, help="Your copy's accuracy stat."),
        "Armor": st.column_config.NumberColumn(min_value=0.0, step=0.01, help="Your copy's armor value."),
        "item_id": st.column_config.NumberColumn(disabled=True),
    }

    try:
        styled = base.style.map(tier_style, subset=["Tier"])
    except AttributeError:
        styled = base.style.applymap(tier_style, subset=["Tier"])

    edited = st.data_editor(
        styled,
        width="stretch",
        hide_index=True,
        key="list_editor",
        column_config=column_config,
    )

    derived_rows: list[dict] = []
    for idx, row in edited.iterrows():
        payload = payload_by_item.get(int(row["item_id"]), {})
        if not payload.get("points"):
            continue
        stats = {
            "damage": row.get("Damage %"),
            "accuracy": row.get("Accuracy %"),
            "armor": row.get("Armor"),
        }
        have = {k: v for k, v in stats.items() if v is not None and not (isinstance(v, float) and pd.isna(v))}
        rq = real_q_by_idx.get(idx)
        q = rq if rq is not None else (quality_from_stats(payload, have) if have else payload.get("median_quality"))
        if q is None:
            continue
        fast, fair, greedy, cur = _stat_suggestion(payload, float(q))
        if cur is None:
            continue
        derived_rows.append(
            {
                "Item": row["Item"],
                "Damage %": stats["damage"],
                "Accuracy %": stats["accuracy"],
                "Armor": stats["armor"],
                "Quality": round(float(q), 2),
                "Fast-sell price": int(round(fast)) if fast else None,
                "Fair price": int(round(fair)) if fair else None,
                "Greedy price": int(round(greedy)) if greedy else None,
            }
        )
    if derived_rows:
        st.subheader("Copy-level prices (from your stats)")
        st.dataframe(pd.DataFrame(derived_rows), width="stretch", hide_index=True)
        n_stat = sum(1 for r in rows_out if r["is_stat"])
        st.caption(
            f"{n_stat} weapon/armor copy(ies) priced on the market's quality curve — edit each copy's "
            "**Damage %** / **Accuracy %** (weapons) or **Armor** (armor) above; prices update here."
        )

    st.caption(
        "Edit the **List price** / **List qty** columns, then build the listing text below. "
        "With stat pricing on, weapon/armor List prices are re-derived from each copy's stats at build time. "
        "Stackable items show one combined row."
    )

    left, right = st.columns([1, 1])
    with left:
        build = st.button("Build listing text")
        st.markdown(f"[Open Torn Item Market — Add Listing]({ADD_LISTING_URL})")
    with right:
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
            (ROOT_DIR / "data" / "tma_autofill.user.js").write_text(userjs, encoding="utf-8")
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
        if build:
            lines = []
            used_stat = 0
            for idx, row in edited.iterrows():
                price = row.get("List price")
                qty = row.get("List qty")
                q = int(qty) if qty is not None and not pd.isna(qty) else 1
                name = str(row.get("Item", "")).strip()
                if not name:
                    continue
                payload = payload_by_item.get(int(row["item_id"]), {})
                if auto_stat and payload.get("points"):
                    stats = {
                        "damage": row.get("Damage %"),
                        "accuracy": row.get("Accuracy %"),
                        "armor": row.get("Armor"),
                    }
                    have = {k: v for k, v in stats.items() if v is not None and not (isinstance(v, float) and pd.isna(v))}
                    rq = real_q_by_idx.get(idx)
                    quality = rq if rq is not None else (quality_from_stats(payload, have) if have else payload.get("median_quality"))
                    if quality is not None:
                        cur = price_at_quality(payload, float(quality)) if payload.get("points") else {}
                        suggested = cur.get(strategy_key)
                        if suggested:
                            price = suggested
                            used_stat += 1
                if price is None or (isinstance(price, float) and pd.isna(price)):
                    continue
                lines.append(f"{name} x{q} @ ${int(round(float(price))):,}")
            if used_stat:
                st.caption(f"{used_stat} row(s) priced from copy stats via the market quality curve.")
            st.session_state["listing_text"] = "\n".join(lines)

    st.divider()
    st.subheader("Special finds")
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
    else:
        df_cand = pd.DataFrame(cand_rows).sort_values("+Premium %", ascending=False)
        min_p = st.slider(
            "Show copies priced at least N% above standard",
            0, 200, 5, 1, key="special_min_premium",
        )
        df_special = df_cand[df_cand["+Premium %"] >= min_p]
        if df_special.empty:
            st.caption("Nothing above that threshold.")
        else:
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
                    "Standard": st.column_config.NumberColumn(format="%,.0f", disabled=True),
                    "Copy price": st.column_config.NumberColumn(format="%,.0f", disabled=True),
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
        )

    if st.session_state.get("listing_text"):
        st.code(st.session_state["listing_text"], language=None)
        st.download_button(
            "Download .txt",
            st.session_state["listing_text"],
            file_name="torn_listings.txt",
            mime="text/plain",
        )

    with st.expander("Auxiliary tables"):
        t_parsed, t_unmatched, t_wide, t_long, t_diag = st.tabs(
            [
                "Parsed items",
                "Unmatched items",
                "Market data (wide)",
                "Market data (long)",
                "Pricing diagnostics",
            ]
        )

        with t_parsed:
            st.dataframe(an["df_parsed"], width="stretch", hide_index=True)

        with t_unmatched:
            if an["unmatched"]:
                df_unmatched = (
                    pd.DataFrame(an["unmatched"], columns=["name", "qty"])
                    .sort_values("name")
                    .reset_index(drop=True)
                )
                st.dataframe(df_unmatched, width="stretch", hide_index=True)
            else:
                st.info("No unmatched items.")

        with t_wide:
            st.dataframe(an["df_market"], width="stretch", hide_index=True)

        with t_long:
            st.dataframe(an["df_long"], width="stretch", hide_index=True)

        with t_diag:
            st.dataframe(df_summary, width="stretch", hide_index=True)


st.set_page_config(page_title="Torn Market Analyzer", layout="centered")

if "api_key" not in st.session_state:
    st.session_state["api_key"] = load_api_key()

st.markdown(
    """
    <style>
      .block-container {
        max-width: 900px;
        margin: 0 auto;
        padding-top: 2rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Torn Market Analyzer")
st.markdown(
    """
    <p style="margin-top:-12px; font-size:0.95rem; color:#666;">
        Under development by
        <a href="https://www.torn.com/profiles.php?XID=3968250" target="_blank" style="text-decoration:none;">
            Kzon [3968250]
        </a>. Coffee tips are always appreciated :)
    </p>
    """,
    unsafe_allow_html=True,
)

with st.expander("How it works"):
    st.markdown(
        """
### How to use the app

1. Go to the Add Listings section of the Item Market.
2. Copy the item list (the part with item names and quantities).
3. Paste it in the text box below; prices and untradable/equipped tags are ignored.
4. Enter your public Torn API key (read-only) so the app can call the itemmarket endpoint.
5. The app fetches up to the first 100 listings per item and computes suggested prices.

Alternatively, switch the source to **API inventory**: the app pulls
your inventory straight from Torn (`user/inventory`). That needs a
**Limited-access** API key with the `user` → `inventory` permission.
Use the scope selector to pull only the categories you care about
(e.g. Weapons + Armor) and cut API usage. **Custom…** lets you tick
only the categories you want — the fastest way to test armour/weapons
when a full weapons scan is too slow.

**Single item** (in the `Inventory source` selector) skips the inventory
call entirely: type an item name or ID and it prices just that one item
in ~2 calls. Add the `uid` of one of your own weapon/armor copies to
price *that exact copy* with its real stats instead of market medians.

Each run updates a step tracker (pull items → read copy stats → market
listings → suggested prices) from start to finish, and once you install
the current autofill script it keeps itself updated from
`http://localhost:9999`.

Once you've run an analysis you can download a **Torn autofill script**
(`.user.js`) — install it once in Tampermonkey and the Torn Add Listing
page fills its own prices, adds a premium for high-quality weapons/armor,
and highlights them.

### What the app does

#### Clean order book
- For each item, up to 100 listings (price + quantity) are loaded.
- Listings are sorted by price and cumulative quantity is tracked to understand market depth.
- Volume is also aggregated by exact price level to detect dominant price walls.

#### Robust center and spread
- A volume-weighted median and quartiles (Q1, Q3) are computed.
- A robust spread (MAD – median absolute deviation) around the median is used to derive robust z-scores.
- This helps flag extremely cheap/expensive prices relative to the bulk of the market.

#### Suspected anchors
- Markets are marked as normal or thin/exclusive based on total units and dominance of a single price level.
- In normal markets, suspected anchors are listings with large |z|, shallow depth and small volume.
- In thin markets, only very high prices far above the median with low volume are flagged.
- These suspected anchors are removed for pricing (unless that would remove everything).

#### Suggested prices
- **Fair price:** clean median (volume-weighted in normal markets, per-listing in thin ones).
- **Greedy price:** clean Q3 (upper quartile) of prices.
- **Fast-sell price:**
  - Thin markets: around the 3rd cheapest clean listing.
  - Unit-style markets: around the N-th cheapest clean listing.
  - Bulk markets: first price where cumulative clean volume reaches a target units threshold.
        """
    )

with st.form("input_form", clear_on_submit=False):
    st.subheader("Item Market listings")
    st.markdown(
        "[Quick access to your listings](https://www.torn.com/page.php?sid=ItemMarket#/addListing)",
        unsafe_allow_html=False,
    )
    st.caption(
        "Run pipeline — progress is tracked live below the button: "
        "1 pull items · 2 read copy stats · 3 market listings · 4 compute prices"
    )

    source = st.radio(
        "Inventory source",
        ("API inventory", "Paste items", "Single item"),
        horizontal=True,
        key="source",
        help="Pull your inventory from Torn, paste the Add Listings text as before, or check one item only.",
    )

    if source == "API inventory":
        st.markdown(
            "Pulls your inventory via `user/inventory`. Needs a Torn key with the "
            "**user → inventory** permission. One row is kept per inventory entry; "
            "equipped items are skipped."
        )
        raw = None

        scope = st.radio(
            "Inventory scope",
            ("Everything", "Weapons + Armor", "Custom…"),
            horizontal=True,
            key="inventory_scope",
            help="Pulling fewer categories means fewer market calls and less data transfer.",
        )
        pull_categories: tuple[str, ...] | None = None
        if scope == "Weapons + Armor":
            pull_categories = ("Primary", "Secondary", "Melee", "Temporary", "Defensive")
        elif scope == "Custom…":
            chosen = st.multiselect(
                "Categories to pull",
                [(CATEGORY_LABELS.get(c, c), c) for c in INVENTORY_CATEGORIES],
                key="inventory_categories",
            )
            pull_categories = tuple(chosen) or None
    elif source == "Paste items":
        raw = st.text_area(
            "Paste your items",
            height=220,
            placeholder="Paste your full Add Listings items text here…",
        )
        pull_categories = None
    else:
        st.markdown(
            "Checks **one item only** — pick it by name or ID, optionally give a weapon/armor copy's "
            "`uid` to price that exact copy. Uses ~2 API calls, so it's handy for spot checks and testing."
        )
        raw = None
        pull_categories = None
        single_query = st.text_input(
            "Item name or ID",
            key="single_query",
            placeholder="e.g. AK-47 or 477",
        )
        single_uid = st.text_input(
            "Your copy UID (optional)",
            key="single_uid",
            placeholder="e.g. 18997743406",
            help="The `uid` of one of your weapon/armor copies (from `torn/{uid}?selections=itemdetails`). "
            "Pricing the copy uses its real stats instead of market medians.",
        )

    api_key = st.text_input(
        "Enter your Torn API key",
        value=st.session_state["api_key"],
        key="api_key_input",
        type="password",
        help="Stored locally in ~/.tma_config.json when \"Remember API key\" is on.",
    )

    remember = st.checkbox(
        "Remember API key on this computer",
        value=bool(st.session_state["api_key"]),
        key="remember_key",
    )

    c1, c2 = st.columns([2, 1])
    with c2:
        restarted = st.form_submit_button("↻ Restart")
    with c1:
        submitted = st.form_submit_button("Run")

if restarted:
    st.session_state.clear()
    st.rerun()

if submitted:
    if not DICT_PATH.exists():
        st.error("Dictionary CSV not found.")
        st.stop()
    if source == "Paste items" and (not raw or not raw.strip()):
        st.error("Listings text is empty.")
        st.stop()
    if source == "Single item" and not st.session_state.get("single_query", "").strip():
        st.error("Enter an item name or ID to check.")
        st.stop()
    api_key = api_key.strip()
    if not api_key:
        st.error("API key required.")
        st.stop()

    st.session_state["api_key"] = api_key
    if remember:
        save_api_key(api_key)
    else:
        clear_api_key()

    sess = session_for_requests()
    bucket = TokenBucket(RATE_LIMIT_PER_MIN)
    clock_run = time.monotonic()
    run_box = st.status("Preparing run…", expanded=True)

    if source == "API inventory":
        planned = len(pull_categories) if pull_categories is not None else len(INVENTORY_CATEGORIES)
        status_box = run_box
        with status_box:
            done_cats = []

            def report(cat: str, phase: str, detail: Optional[int]) -> None:
                if phase == "done":
                    done_cats.append(cat)
                    status_box.update(
                        label=f"Step 1/4 · pulling inventory {len(done_cats)}/{planned} categories",
                        state="running",
                    )
                    st.caption(f"{cat}: {detail} items")

            inv = fetch_inventory(sess, api_key, categories=pull_categories, on_progress=report, bucket_take=bucket.take)
            for err in inv.errors:
                st.caption(f"inventory: {err}")
            entries, df_parsed, agg = to_rows(inv.entries)
            unmatched: list = []
            if not entries:
                run_box.update(label="Inventory empty", state="error")
                st.warning("No tradable inventory found via the API.")
                st.stop()

            uid_stats: dict = {}
            uid_errors: list[str] = []
            uid_item = {str(e.uid): e.item_id for e in entries if e.uid}
            if uid_item:
                profiles_early = load_profiles(PROFILES_PATH)
                stat_ids = {int(pid) for pid, prof in profiles_early.items() if prof.get("type") in ("Weapon", "Armor")}
                stats_targets = sorted(uid for uid, iid in uid_item.items() if iid in stat_ids)
                if stats_targets:

                    def stat_report(done: int, total: int) -> None:
                        status_box.update(label=f"Step 2/4 · reading copy stats {done}/{total}…", state="running")

                    uid_stats, uid_errors = fetch_item_stats_many(
                        sess, api_key, stats_targets, bucket_take=bucket.take, on_progress=stat_report
                    )
                    for err in uid_errors:
                        st.caption(f"copy stats: {err}")
                    st.caption(f"Read individual stats for {len(uid_stats)} weapon/armor copies.")
        status_box.update(
            label=f"Step 2/4 · inventory ready ({len(entries)} items)"
            + (f" · {len(inv.errors)} errors" if inv.errors else ""),
            state="complete",
        )
        items = [(e.item_id, e.amount, e.name) for e in entries]
    elif source == "Paste items":
        run_box.update(label="Step 1/4 · parsing & matching items…", state="running")
        with st.spinner("Parsing & matching…"):
            result = match_inventory(raw, DICT_PATH)
            if not result.matched:
                st.warning("No matches found.")
                st.stop()

            df_parsed = (
                pd.DataFrame([{"name": it.name, "id": it.item_id, "quantity": it.qty} for it in result.matched])
                .sort_values("name")
                .reset_index(drop=True)
            )

            agg = [(it.item_id, it.qty) for it in result.matched]
            items = [(it.item_id, it.qty, it.name) for it in result.matched]
            if not agg:
                st.warning("No valid item IDs after matching.")
                st.stop()
            unmatched = result.unmatched
            entries = None
            uid_stats = {}
        run_box.update(label=f"Step 1/4 · matched {len(items)} items", state="complete")
    else:
        run_box.update(label="Step 1/4 · resolving item…", state="running")
        with st.spinner("Resolving item…"):
            query = st.session_state.get("single_query", "").strip()
            name_to_id = load_dictionary(DICT_PATH)
            id_to_name: dict[int, str] = {}
            for nm, iid in name_to_id.items():
                id_to_name.setdefault(iid, nm)

            if query.isdigit():
                iid = int(query)
                single_name = id_to_name.get(iid)
                if single_name is None:
                    st.error(f"No dictionary entry for item ID {query}.")
                    st.stop()
                resolved = [(single_name, iid)]
            else:
                low = query.lower()
                exact = [n for n in name_to_id if n.lower() == low]
                cands = sorted(exact or [n for n in name_to_id if low in n.lower()])
                if len(cands) == 1:
                    resolved = [(cands[0], name_to_id[cands[0]])]
                elif len(cands) > 1:
                    shown = ", ".join(cands[:20])
                    st.error(f"{query!r} matches {len(cands)} items — be more specific. e.g. {shown}…")
                    st.stop()
                else:
                    st.error(f"No item matches {query!r}.")
                    st.stop()

            single_name, iid = resolved[0]
            single_uid = st.session_state.get("single_uid", "").strip() or None
            entry = InventoryEntry(item_id=iid, name=single_name, amount=1, equipped=False, uid=single_uid)
            entries = [entry]
            df_parsed = pd.DataFrame([{"name": single_name, "id": iid, "quantity": 1}])
            agg = [(iid, 1)]
            items = [(iid, 1, single_name)]
            unmatched = []
            uid_stats = {}
            if single_uid:
                stats = fetch_item_stats(sess, api_key, single_uid, bucket_take=bucket.take)
                if stats:
                    uid_stats[single_uid] = stats
                else:
                    st.caption(f"Couldn't read stats for copy uid {single_uid}; using market medians.")
        run_box.update(label="Step 1/4 · item resolved" + (" · copy stats read" if single_uid else ""), state="complete")

    per_id_qty: dict[int, int] = {}
    for iid, qty in agg:
        per_id_qty[iid] = per_id_qty.get(iid, 0) + qty
    unique_ids = list(per_id_qty)
    n_uniq = len(unique_ids)

    run_box.update(label=f"Step 3/4 · pricing {n_uniq} distinct items…", state="running")
    bar = st.progress(0.0, text="Pricing items…")
    market_start = time.monotonic()
    trunc_after = None
    market_errors: list[str] = []
    per_id_rows: dict[int, dict] = {}
    for i, iid in enumerate(unique_ids, start=1):
        row = fetch_100(sess, bucket, api_key, iid, per_id_qty[iid])
        if "error" in row:
            market_errors.append(str(row["error"]))
        per_id_rows[iid] = row

        elapsed = time.monotonic() - market_start
        eta = ""
        if elapsed > 3.0:
            rate = i / elapsed
            eta = f" · ETA ~{(n_uniq - i) / rate / 60.0:.0f} min"
        bar.progress(i / n_uniq, text=f"Priced {i}/{n_uniq}: {row.get('item_name') or iid}{eta}")

        if elapsed > MARKET_PHASE_TIMEOUT_S:
            trunc_after = round(elapsed / 60.0, 1)
            break

    if trunc_after is not None:
        st.warning(f"Market phase stopped after {trunc_after} min; priced {len(per_id_rows)}/{n_uniq} distinct items.")
    run_box.update(
        label=f"Step 3/4 · priced {len(per_id_rows)}/{n_uniq} distinct items"
        + (f" · {len(market_errors)} errors" if market_errors else ""),
        state="complete",
    )
    for err in market_errors:
        st.caption(f"market: {err}")

    if not per_id_rows:
        run_box.update(label="Market phase failed", state="error")
        st.error("No market data was retrieved.")
        st.stop()

    rows = [dict(per_id_rows[iid], my_quantity=qty) for iid, qty in agg if iid in per_id_rows]
    df_market = pd.DataFrame(rows)

    for i in range(1, 101):
        pcol = f"price_{i}"
        acol = f"amount_{i}"
        if pcol in df_market.columns:
            df_market[pcol] = pd.to_numeric(df_market[pcol], errors="coerce")
        if acol in df_market.columns:
            df_market[acol] = pd.to_numeric(df_market[acol], errors="coerce")
        for scol in (f"quality_{i}", f"damage_{i}", f"accuracy_{i}", f"armor_{i}"):
            if scol in df_market.columns:
                df_market[scol] = pd.to_numeric(df_market[scol], errors="coerce")

    for c in ("average_price", "my_quantity", "item_id"):
        if c in df_market.columns:
            df_market[c] = pd.to_numeric(df_market[c], errors="coerce")

    run_box.update(label="Step 4/4 · computing price suggestions…", state="running")
    with st.spinner("Computing price suggestions…"):
        df_long = wide_to_long(df_market)
        if df_long.empty:
            run_box.update(label="No valid listings found", state="error")
            st.warning("No valid listings found in the market data.")
            st.stop()

        df_enriched = enrich_all_items(df_long)
        df_summary = build_summary_from_enriched(df_enriched).sort_values("item_name").reset_index(drop=True)

        profiles, stat_model = load_stat_layer()
        warnings = stat_warnings(stat_model, profiles, df_summary)

        stat_cols = []
        for iid in df_summary["item_id"]:
            try:
                info = sidecar(stat_model, profiles, int(iid))
            except (TypeError, ValueError):
                stat_cols.append(("—", None, None))
                continue
            stat_cols.append((info.tier or "—", info.market_price, info.implied_value))
        df_summary["stat_tier"] = [c[0] for c in stat_cols]
        df_summary["market_price_ref"] = [c[1] for c in stat_cols]
        df_summary["stat_implied"] = [c[2] for c in stat_cols]

        st.session_state["analysis"] = {
            "df_summary": df_summary,
            "warnings": warnings,
            "df_parsed": df_parsed,
            "unmatched": unmatched,
            "df_market": df_market,
            "df_long": df_long,
            "profiles": profiles,
            "entries": entries,
            "uid_stats": uid_stats if source in ("API inventory", "Single item") else {},
        }
        st.session_state["has_data"] = True

        run_box.update(
            label=f"Done · priced {len(per_id_rows)} distinct items "
            f"in {(time.monotonic() - clock_run) / 60.0:.1f} min",
            state="complete",
        )

if st.session_state.get("has_data"):
    render_results(st.session_state["analysis"])