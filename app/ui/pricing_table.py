"""Primary results tab: the price-suggestion table, editable list price/qty,
and building the plain-text listing to paste into Torn.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tma.market_enrichment import price_at_quality, quality_from_stats

from .format import fmt_int

ADD_LISTING_URL = "https://www.torn.com/page.php?sid=ItemMarket#/addListing"

TIER_COLORS = {
    "Common": "#e3e3e3",
    "Solid": "#c8e6c9",
    "Premium": "#bbdefb",
    "Rare": "#e1bee7",
    "Elite": "#ffd97a",
}

LIST_BASIS_OPTIONS = ("Fast-sell price", "Fair price", "Greedy price")
_STRATEGY_KEY = {"Fast-sell price": "fast", "Fair price": "fair", "Greedy price": "greedy"}
_STRATEGY_COL = {"Fast-sell price": "fast_sell_price", "Fair price": "fair_price", "Greedy price": "greedy_price"}


def tier_style(v):
    color = TIER_COLORS.get(v)
    if not color:
        return ""
    return f"background-color: {color}; font-weight: 600;"


_LIST_EDITOR_KEY = "list_editor"


def render_warnings_banner(warnings: list[dict]) -> None:
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


def render_pricing_table(an: dict) -> dict:
    """Renders the price-suggestion table + listing-text builder.

    Returns a context dict consumed by the Special finds and Autofill tabs
    so they don't have to recompute per-copy stat pricing.
    """
    df_summary = an["df_summary"]

    strategy = st.radio(
        "List price basis",
        LIST_BASIS_OPTIONS,
        index=0,
        horizontal=True,
        key="list_strategy",
        help="Weapon/armor copies are priced on the market's own quality curve from each copy's stats; other items use this basis for the pre-filled price.",
    )
    strategy_key = _STRATEGY_KEY[strategy]
    strategy_col = _STRATEGY_COL[strategy]

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
                        "Market": ADD_LISTING_URL,
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
                        "uid": copy.uid if copy else None,
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
                    "Market": ADD_LISTING_URL,
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
        key=_LIST_EDITOR_KEY,
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

    build = st.button("Build listing text")
    st.markdown(f"[Open Torn Item Market — Add Listing]({ADD_LISTING_URL})")

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

    if st.session_state.get("listing_text"):
        st.code(st.session_state["listing_text"], language=None)
        st.download_button(
            "Download .txt",
            st.session_state["listing_text"],
            file_name="torn_listings.txt",
            mime="text/plain",
            key="download_listing_text",
        )

    return {
        "edited": edited,
        "rows_out": rows_out,
        "payload_by_item": payload_by_item,
        "real_q_by_idx": real_q_by_idx,
        "strategy": strategy,
        "strategy_key": strategy_key,
        "strategy_col": strategy_col,
    }
