import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app.paths import DICT_PATH, PROFILES_PATH
from app.ui.results import render_results
from tma.config import load_api_key, save_api_key, clear_api_key
from tma.http_client import TokenBucket, session_for_requests
from tma.inventory import (
    fetch_inventory,
    fetch_item_stats,
    fetch_item_stats_many,
    inventory_category_for_profile,
    to_rows,
    InventoryEntry,
    INVENTORY_CATEGORIES,
    CATEGORY_LABELS,
)
from tma.inventory_matcher import load_dictionary, match_inventory
from tma.market_enrichment import wide_to_long, enrich_all_items, build_summary_from_enriched
from tma.market_fetch import fetch_all_item_markets
from tma.item_stats import StatValueModel, load_profiles, sidecar, stat_warnings

MAX_WORKERS = 5
RATE_LIMIT_PER_MIN = 90
MARKET_PHASE_TIMEOUT_S = 1200.0


@st.cache_data
def load_stat_layer() -> tuple[dict, StatValueModel]:
    profiles = load_profiles(PROFILES_PATH)
    model = StatValueModel()
    model.fit(profiles)
    return profiles, model


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

**Single item** (in the `Inventory source` selector) prices just that one
item: type a name or ID and it looks up how many you actually own (one
inventory category, not a full scan) along with the market price. Add the
`uid` of one of your own weapon/armor copies to price *that exact copy*
with its real stats instead of market medians — that skips the inventory
lookup entirely, since you're pricing one specific copy either way.

Each run updates a step tracker (pull items → read copy stats → market
listings → suggested prices) from start to finish.

Once you've run an analysis, the **Autofill script** tab has a link that
installs the **Torn autofill script** straight into Tampermonkey — no
download, no manual reinstall. Click it once to install; after every later
run, click it again and Tampermonkey shows an instant update prompt with
the new prices. On the Add Listing page it fills each row's price, adds a
premium for high-quality weapons/armor, and highlights them.

For a pile of ordinary items you just want off your hands, use the
**Bulk sell** tab — pick a discount (e.g. 75% of Fair price), uncheck
anything you'd rather keep at full price, and copy the listing text.

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

st.subheader("Item Market listings")
st.markdown(
    "[Quick access to your listings](https://www.torn.com/page.php?sid=ItemMarket#/addListing)",
    unsafe_allow_html=False,
)
st.caption(
    "Run pipeline — progress is tracked live below the button: "
    "1 pull items · 2 read copy stats · 3 market listings · 4 compute prices"
)

# Kept outside the form: widgets inside st.form() only trigger a rerun on
# submit, so a radio choice made here wouldn't reveal its matching inputs
# (e.g. the Single item text box) until a first, doomed submit attempt.
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
        "`uid` to price that exact copy instead. Handy for spot checks without a full inventory scan."
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

with st.form("input_form", clear_on_submit=False):
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
            unmatched = []
            uid_stats = {}

            if single_uid:
                # An explicit copy uid means "price this one copy", regardless
                # of how many of the item the account actually owns.
                entries = [InventoryEntry(item_id=iid, name=single_name, amount=1, equipped=False, uid=single_uid)]
                df_parsed = pd.DataFrame([{"name": single_name, "id": iid, "quantity": 1}])
                agg = [(iid, 1)]
                items = [(iid, 1, single_name)]
                stats = fetch_item_stats(sess, api_key, single_uid, bucket_take=bucket.take)
                if stats:
                    uid_stats[single_uid] = stats
                else:
                    st.caption(f"Couldn't read stats for copy uid {single_uid}; using market medians.")
            else:
                profile = load_profiles(PROFILES_PATH).get(str(iid))
                category = inventory_category_for_profile(profile)
                owned: list[InventoryEntry] = []
                if category:
                    inv = fetch_inventory(sess, api_key, categories=(category,), bucket_take=bucket.take)
                    for err in inv.errors:
                        st.caption(f"inventory: {err}")
                    owned = [e for e in inv.entries if e.item_id == iid and not e.equipped]

                if owned:
                    entries = owned
                    qty = sum(e.amount for e in owned)
                    if profile and profile.get("type") in ("Weapon", "Armor"):
                        uid_targets = sorted({e.uid for e in owned if e.uid})
                        if uid_targets:
                            uid_stats, uid_errors = fetch_item_stats_many(sess, api_key, uid_targets, bucket_take=bucket.take)
                            for err in uid_errors:
                                st.caption(f"copy stats: {err}")
                            st.caption(f"Read individual stats for {len(uid_stats)} of your {len(owned)} copies.")
                elif category is None:
                    st.caption("Couldn't determine this item's inventory category; pricing for qty 1, not your real stock.")
                    entries = [InventoryEntry(item_id=iid, name=single_name, amount=1, equipped=False, uid=None)]
                    qty = 1
                else:
                    st.caption("You don't own any non-equipped copies right now; pricing for qty 1, not your real stock.")
                    entries = [InventoryEntry(item_id=iid, name=single_name, amount=1, equipped=False, uid=None)]
                    qty = 1

                df_parsed = pd.DataFrame([{"name": single_name, "id": iid, "quantity": qty}])
                agg = [(iid, qty)]
                items = [(iid, qty, single_name)]
        run_box.update(label="Step 1/4 · item resolved" + (" · copy stats read" if single_uid else ""), state="complete")

    per_id_qty: dict[int, int] = {}
    for iid, qty in agg:
        per_id_qty[iid] = per_id_qty.get(iid, 0) + qty
    unique_ids = list(per_id_qty)
    n_uniq = len(unique_ids)

    run_box.update(label=f"Step 3/4 · pricing {n_uniq} distinct items…", state="running")
    bar = st.progress(0.0, text="Pricing items…")
    market_start = time.monotonic()
    market_errors: list[str] = []

    def _on_market_progress(done: int, total: int, row: dict) -> None:
        if "error" in row:
            market_errors.append(str(row["error"]))
        elapsed = time.monotonic() - market_start
        eta = ""
        if elapsed > 3.0:
            rate = done / elapsed
            eta = f" · ETA ~{(total - done) / rate / 60.0:.0f} min"
        bar.progress(done / total, text=f"Priced {done}/{total}: {row.get('item_name') or row.get('item_id')}{eta}")

    per_id_rows, truncated = fetch_all_item_markets(
        sess,
        bucket,
        api_key,
        unique_ids,
        per_id_qty,
        max_workers=MAX_WORKERS,
        on_progress=_on_market_progress,
        deadline_s=MARKET_PHASE_TIMEOUT_S,
    )

    if truncated:
        elapsed_min = round((time.monotonic() - market_start) / 60.0, 1)
        st.warning(f"Market phase stopped after {elapsed_min} min; priced {len(per_id_rows)}/{n_uniq} distinct items.")
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
