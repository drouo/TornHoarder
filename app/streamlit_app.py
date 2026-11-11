import re
import sys
import time
from dataclasses import replace
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

from app.paths import (
    AUTOFILL_PATH, DICT_PATH, MUSEUM_PATH, OC_ITEMS_PATH, PRICE_HISTORY_PATH, PROFILES_PATH, SHOP_BUYS_PATH,
    UID_STATS_PATH,
)
from app.ui import autofill_export
from app.ui.results import render_results
from tma import __version__
from tma.catalog import refresh_catalog_if_stale
from tma import museum, my_listings
from tma.crime_items import refresh_oc_items_if_stale
from tma.config import load_api_key, save_api_key, clear_api_key, load_sell_prefs, save_sell_prefs
from tma.sell_plan import SellRules
from tma.http_client import TokenBucket, session_for_requests
from tma.inventory import (
    fetch_inventory,
    fetch_item_stats_cached,
    inventory_category_for_profile,
    to_rows,
    InventoryEntry,
    INVENTORY_CATEGORIES,
    CATEGORY_LABELS,
)
from tma.inventory_matcher import load_dictionary, match_inventory
from tma.price_history import append_history, history_rows, tracked_item_ids
from tma.shops import load_shop_knowledge, shop_price_for
from tma.market_enrichment import wide_to_long, enrich_all_items, build_summary_from_enriched
from tma.market_fetch import fetch_all_item_markets
from tma.item_stats import StatValueModel, load_profiles, sidecar, stat_warnings

MAX_WORKERS = 5
RATE_LIMIT_PER_MIN = 90
MARKET_PHASE_TIMEOUT_S = 1200.0


def script_version(path) -> Optional[str]:
    """The @version of the last-built userscript, or None if there isn't one."""
    try:
        head = Path(path).read_text(encoding="utf-8")[:2000]
    except OSError:
        return None
    m = re.search(r"@version\s+(\S+)", head)
    return m.group(1) if m else None


def _numeric_market(df_market: pd.DataFrame) -> pd.DataFrame:
    for i in range(1, 101):
        for col in (f"price_{i}", f"amount_{i}", f"quality_{i}", f"damage_{i}", f"accuracy_{i}", f"armor_{i}"):
            if col in df_market.columns:
                df_market[col] = pd.to_numeric(df_market[col], errors="coerce")
    for c in ("average_price", "my_quantity", "item_id"):
        if c in df_market.columns:
            df_market[c] = pd.to_numeric(df_market[c], errors="coerce")
    return df_market


@st.cache_data
def load_stat_layer(profiles_mtime: float) -> tuple[dict, StatValueModel]:
    # profiles_mtime only keys the cache, so a catalog refresh refits the model.
    profiles = load_profiles(PROFILES_PATH)
    model = StatValueModel()
    model.fit(profiles)
    return profiles, model


st.set_page_config(page_title=f"TornHoarder v{__version__}", layout="centered")

# Serve the autofill script for Tampermonkey's update checks from app start,
# not only once a finished run has rendered the Autofill tab.
autofill_export._ensure_update_server()

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

st.title("TornHoarder")
st.markdown(
    f"""
    <p style="margin-top:-12px; font-size:0.95rem; color:#666;">
        <b>v{__version__}</b> ·
        Originally created by
        <a href="https://www.torn.com/profiles.php?XID=3968250" target="_blank" style="text-decoration:none;">
            Kzon [3968250]
        </a> · maintained by
        <a href="https://www.torn.com/profiles.php?XID=3653623" target="_blank" style="text-decoration:none;">
            TheOldFarmer [3653623]
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
**Sell categories** is the list of what you're willing to sell: only those
categories are pulled and priced, and everything else (Xanax, medical,
boosters, cans, books, ...) is never listed. Equipped gear is always skipped.

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

The **Sell plan** tab gives every item a plan automatically: **Dump**
cheap junk a few % under the cheapest listing so it clears fast,
**Competitive** (cheapest − $1) for anything worth $20k+ or a big stack,
**Top dollar** for high-roll weapons/armor and valuable items with few
listings, and **Shop** when a city shop pays more than the market after
Torn's 5% sales tax. Change any item's plan (or set it to **Keep**) and
it's remembered next run; the autofill script lists items by their plan.

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
- **Fast-sell price:** $1 under the cheapest clean listing — buyers take the cheapest listing first.
        """
    )

served = script_version(AUTOFILL_PATH)
if served and served != __version__:
    st.warning(
        f"Your Tampermonkey script is v{served}; this app is v{__version__}. Click **Run** to build the new "
        "script, then use Tampermonkey's *Check for userscript updates*."
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

    # Sell categories are an allowlist: only these are pulled and priced, so
    # anything you'd never sell (Xanax, medical, boosters, ...) never even
    # reaches the autofill script. Saved with the rest of the sell rules.
    saved_rules, saved_overrides = load_sell_prefs()
    rules_now = SellRules.from_dict(saved_rules)
    chosen = st.multiselect(
        "Sell categories",
        INVENTORY_CATEGORIES,
        default=[c for c in rules_now.sell_categories if c in INVENTORY_CATEGORIES],
        format_func=lambda c: CATEGORY_LABELS.get(c, c),
        key="sell_categories",
        help="Only these categories are pulled and priced; everything else is never listed. "
        "Equipped items are always skipped. Remembered on this computer.",
    )
    tracked = st.multiselect(
        "Track price history for",
        INVENTORY_CATEGORIES,
        default=[c for c in rules_now.history_categories if c in INVENTORY_CATEGORIES],
        format_func=lambda c: CATEGORY_LABELS.get(c, c),
        key="history_categories",
        help="Every run adds one row per priced item to data/price_history.csv. Items you own are always "
        "recorded; these categories add every item in them, owned or not (one extra market call each).",
    )
    if tuple(chosen) != rules_now.sell_categories or tuple(tracked) != rules_now.history_categories:
        save_sell_prefs(
            replace(rules_now, sell_categories=tuple(chosen), history_categories=tuple(tracked)).to_dict(),
            saved_overrides,
        )
    pull_categories: tuple[str, ...] | None = tuple(chosen)
    history_categories: tuple[str, ...] = tuple(tracked)
elif source == "Paste items":
    history_categories = ()
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
    history_categories = ()
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
    if source == "API inventory" and not pull_categories:
        st.error("Pick at least one sell category.")
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

    with run_box:
        refreshed, cat_err = refresh_catalog_if_stale(sess, api_key, DICT_PATH, PROFILES_PATH)
        if refreshed:
            st.caption("Refreshed the item catalog (reference and shop prices) from Torn.")
        elif cat_err:
            st.caption(f"Couldn't refresh the item catalog ({cat_err}); using the saved copy.")
        oc_refreshed, oc_err = refresh_oc_items_if_stale(sess, api_key, OC_ITEMS_PATH)
        if oc_refreshed:
            st.caption("Refreshed the list of items Organized Crimes need.")
        elif oc_err:
            st.caption(f"Couldn't refresh the Organized Crime item list ({oc_err}); using the saved copy.")
        _, museum_err = museum.refresh_museum_if_stale(sess, api_key, MUSEUM_PATH)
        if museum_err:
            st.caption(f"Couldn't refresh the museum sets ({museum_err}); using the saved copy.")
        point_price, point_err = museum.fetch_point_price(sess, api_key)
        if point_err:
            st.caption(f"Couldn't read the points market ({point_err}); museum sets can't be compared.")

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

                    uid_stats, uid_errors, n_cached = fetch_item_stats_cached(
                        sess, api_key, stats_targets, UID_STATS_PATH,
                        bucket_take=bucket.take, on_progress=stat_report, prune=True, item_ids=uid_item,
                    )
                    for err in uid_errors:
                        st.caption(f"copy stats: {err}")
                    st.caption(
                        f"Stats for {len(uid_stats)} weapon/armor copies: {n_cached} remembered, "
                        f"{len(uid_stats) - n_cached} read from Torn."
                    )
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
                stats = fetch_item_stats_cached(
                    sess, api_key, [single_uid], UID_STATS_PATH, bucket_take=bucket.take, item_ids={single_uid: iid}
                )[0]
                if stats.get(str(single_uid)):
                    uid_stats[single_uid] = stats[str(single_uid)]
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
                            uid_stats, uid_errors, _ = fetch_item_stats_cached(
                                sess, api_key, uid_targets, UID_STATS_PATH, bucket_take=bucket.take
                            )
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
    # Your active listings are checked against the market too; their items
    # aren't in your inventory any more, so they're fetched as extras.
    listed: list[dict] = []
    if source == "API inventory":
        listed, listed_err = my_listings.fetch_my_listings(sess, api_key, bucket)
        if listed_err:
            st.caption(f"Couldn't read your Item Market listings ({listed_err}).")
    listed_ids = {int((l.get("item") or {}).get("id")) for l in listed if (l.get("item") or {}).get("id")}
    # Tracked items you don't own ride along in the same market fetch, for
    # price history only; they never reach the sell plan.
    extra_ids = sorted(
        set(tracked_item_ids(load_profiles(PROFILES_PATH), history_categories, exclude=per_id_qty))
        | (listed_ids - set(per_id_qty))
    )
    unique_ids = list(per_id_qty) + extra_ids
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
        {**per_id_qty, **{iid: 0 for iid in extra_ids}},
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

    # per_id_rows can hold error placeholders (no listings data) alongside
    # successful fetches — drop those before building the market table, or
    # a wholly-failed run silently produces a table with none of the
    # expected columns instead of a clear error.
    rows = [
        dict(per_id_rows[iid], my_quantity=qty)
        for iid, qty in agg
        if iid in per_id_rows and "error" not in per_id_rows[iid]
    ]
    if not rows:
        run_box.update(label="Market phase failed", state="error")
        st.error("Every item lookup failed — no market data was retrieved. See the errors above.")
        st.stop()
    df_market = _numeric_market(pd.DataFrame(rows))

    run_box.update(label="Step 4/4 · computing price suggestions…", state="running")
    with st.spinner("Computing price suggestions…"):
        df_long = wide_to_long(df_market)
        if df_long.empty:
            run_box.update(label="No valid listings found", state="error")
            st.warning("No valid listings found in the market data.")
            st.stop()

        df_enriched = enrich_all_items(df_long)
        df_summary = build_summary_from_enriched(df_enriched).sort_values("item_name").reset_index(drop=True)

        profiles, stat_model = load_stat_layer(PROFILES_PATH.stat().st_mtime)
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

        df_hist = df_summary
        extra_rows = [dict(per_id_rows[i], my_quantity=0) for i in extra_ids if i in per_id_rows and "error" not in per_id_rows[i]]
        df_long_hist = df_long
        if extra_rows:
            extra_long = wide_to_long(_numeric_market(pd.DataFrame(extra_rows)))
            if not extra_long.empty:
                df_hist = pd.concat([df_summary, build_summary_from_enriched(enrich_all_items(extra_long))], ignore_index=True)
                df_long_hist = pd.concat([df_long, extra_long], ignore_index=True)
        n_hist = append_history(PRICE_HISTORY_PATH, history_rows(df_hist, df_long_hist, profiles, time.time()))
        st.caption(f"Price history: {n_hist} rows added ({len(extra_rows)} tracked items you don't own).")

        known_shops = load_shop_knowledge(SHOP_BUYS_PATH)
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
            "museum": {"sets": museum.load_museum(MUSEUM_PATH), "point_price": point_price},
            "my_listings": [
                my_listings.review_listing(
                    l,
                    my_listings.market_listings(per_id_rows.get(int(l["item"]["id"])) or {}),
                    shop_price_for(profiles.get(str(l["item"]["id"])), int(l["item"]["id"]), known_shops),
                    (profiles.get(str(l["item"]["id"])) or {}).get("market_price"),
                )
                for l in listed
                if (l.get("item") or {}).get("id")
            ],
        }
        st.session_state["has_data"] = True

        run_box.update(
            label=f"Done · priced {len(per_id_rows)} distinct items "
            f"in {(time.monotonic() - clock_run) / 60.0:.1f} min",
            state="complete",
        )

if st.session_state.get("has_data"):
    render_results(st.session_state["analysis"])
