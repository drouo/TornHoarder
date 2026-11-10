"""Sell plan tab: every item gets an automatic plan (Dump / Competitive /
Top dollar / Shop / Keep) with a list price to match. Changing an item's
Plan is remembered across runs (~/.tma_config.json); the resulting prices
feed the autofill script.

Replaces the old Bulk sell tab, whose flat "% of Fair price" was either
still above the floor (never sells) or far below it (gives money away).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tma.config import load_sell_prefs, save_sell_prefs
from tma.inventory import inventory_category_for_profile
from tma.sell_plan import LABEL_TO_PLAN, MARKET_FEE, PLAN_LABELS, PLANS, Decision, SellRules, decide, price_guards

_EDITOR_KEY = "sell_plan_editor"


def _num(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def is_sellable(profile: dict, rules: SellRules) -> bool:
    """Only items in a chosen sell category get a market/shop plan; unknown -> keep."""
    return inventory_category_for_profile(profile) in rules.sell_categories


def plan_rows(
    rows_out: list[dict],
    real_q_by_idx: dict[int, float],
    summary_by_id: dict[int, dict],
    profiles: dict,
    rules: SellRules,
    overrides: dict[int, str],
) -> list[dict]:
    """One row per pricing-table row (stackables combined, one per weapon/armor copy)."""
    out = []
    for idx, ro in enumerate(rows_out):
        iid = int(ro["item_id"])
        prof = profiles.get(str(iid)) or {}
        summ = summary_by_id.get(iid) or {}
        qty = int(_num(ro.get("My quantity")) or 0)
        kwargs = dict(
            qty=qty,
            fast=_num(ro.get("Fast-sell price")),
            fair=_num(ro.get("Fair price")),
            greedy=_num(ro.get("Greedy price")),
            shop_price=prof.get("sell_price"),
            num_listings=_num(summ.get("num_clean_listings")),
            quality=real_q_by_idx.get(idx) if ro.get("is_stat") else None,
            rules=rules,
            ref_price=prof.get("market_price"),
            sellable=is_sellable(prof, rules),
        )
        auto: Decision = decide(**kwargs)
        d: Decision = decide(**kwargs, override=overrides.get(iid)) if iid in overrides else auto
        out.append(
            {
                "item_id": iid,
                "is_stat": bool(ro.get("is_stat")),
                "auto_plan": auto.plan,
                "Plan": PLAN_LABELS[d.plan],
                "Item": ro["Item"],
                "Qty": qty,
                "List price": d.price,
                "Why": d.reason,
                "Floor": _num(summ.get("floor_price")),
                "Fair": kwargs["fair"],
                "Shop pays": prof.get("sell_price") or None,
                "Est. proceeds": int(round(d.proceeds)),
                "Stack value": float(kwargs["fair"] or kwargs["fast"] or 0.0) * qty,
            }
        )
    return out


def item_plans(
    df_summary: pd.DataFrame,
    edited: pd.DataFrame,
    profiles: dict,
    rules: SellRules,
    overrides: dict[int, str],
) -> dict[int, dict]:
    """Per-item plan for the autofill script.

    Plain items carry the (possibly hand-edited) list price. Weapon/armor
    items carry only the plan: the script prices each row from that copy's
    rolls, and bumps an automatic plan to Top dollar for high-roll copies.
    """
    stat_ids = {int(i) for i in edited.loc[edited["is_stat"], "item_id"]}
    plans: dict[int, dict] = {}
    for _, r in df_summary.iterrows():
        iid = int(r["item_id"])
        prof = profiles.get(str(iid)) or {}
        d = decide(
            qty=int(_num(r.get("my_quantity")) or 0),
            fast=_num(r.get("fast_sell_price")),
            fair=_num(r.get("fair_price")),
            greedy=_num(r.get("greedy_price")),
            shop_price=prof.get("sell_price"),
            num_listings=_num(r.get("num_clean_listings")),
            quality=None,
            rules=rules,
            override=overrides.get(iid),
            # Weapon/armor: the cheapest junk copy vs Torn's all-quality average
            # would hold every copy; the script applies refMin per copy instead.
            ref_price=None if iid in stat_ids else prof.get("market_price"),
            sellable=is_sellable(prof, rules),
        )
        shop_min, ref_min = price_guards(prof.get("sell_price"), prof.get("market_price"))
        plans[iid] = {
            "plan": d.plan,
            "auto": d.auto,
            "list": d.price,
            "shopMin": shop_min,
            # A plan you picked yourself is listed anyway (the app shows the warning).
            "refMin": ref_min if d.auto else None,
        }
    for _, row in edited.iterrows():
        iid = int(row["item_id"])
        if iid in plans and not row["is_stat"]:
            price = _num(row.get("List price"))
            plans[iid]["list"] = int(price) if price else None
            plans[iid]["qty"] = int(row["Qty"])
    return plans


def overrides_from_edit(base: pd.DataFrame, edited: pd.DataFrame, overrides: dict[int, str]) -> dict[int, str]:
    """Apply only the rows whose Plan the user actually changed.

    Weapon/armor items have one row per copy, each with its own auto plan, so
    looking at untouched rows would let one copy's row undo another's override.
    Picking a row's own auto plan clears the override instead of storing it.
    """
    new = dict(overrides)
    for i in edited.index[edited["Plan"] != base["Plan"]]:
        row = edited.loc[i]
        chosen = LABEL_TO_PLAN.get(row["Plan"])
        if chosen is None:
            continue
        iid = int(row["item_id"])
        if chosen == row["auto_plan"]:
            new.pop(iid, None)
        else:
            new[iid] = chosen
    return new


def _render_rules(saved: dict) -> SellRules:
    base = SellRules.from_dict(saved)
    with st.expander("Rules"):
        c1, c2 = st.columns(2)
        with c1:
            undercut = st.number_input(
                "Dump: % under the cheapest listing", 0.0, 50.0, float(base.dump_undercut_pct), 1.0, key="rule_undercut"
            )
            unit = st.number_input(
                "Competitive from (per unit, $)", 0, None, int(base.competitive_min_unit), 5_000, key="rule_unit"
            )
            stack = st.number_input(
                "…or a stack worth ($)", 0, None, int(base.competitive_min_stack), 100_000, key="rule_stack"
            )
        with c2:
            quality = st.number_input(
                "Top dollar: copy quality ≥", 0.0, 200.0, float(base.top_min_quality), 5.0, key="rule_quality"
            )
            thin = st.number_input(
                "Top dollar: competitive items with fewer listings than", 1, 100, int(base.thin_market_listings), 1,
                key="rule_thin",
            )
        st.caption(
            f"Market proceeds are after Torn's {MARKET_FEE:.0%} item-market sales tax. Dump/Competitive items switch "
            "to **Shop** automatically when a city shop pays at least that much."
        )
    return SellRules(float(undercut), float(unit), float(stack), float(quality), int(thin), base.sell_categories)


def render_sell_plan(an: dict, ctx: dict) -> dict:
    saved_rules, overrides = load_sell_prefs()
    rules = _render_rules(saved_rules)

    summary_by_id = {int(r["item_id"]): r for r in an["df_summary"].to_dict("records")}
    profiles = an.get("profiles") or {}
    rows = plan_rows(ctx["rows_out"], ctx["real_q_by_idx"], summary_by_id, profiles, rules, overrides)
    # data_editor stores edits by row position, so the order must not depend
    # on the plan (which edits change) — stack value doesn't.
    df = pd.DataFrame(rows)
    df = df.sort_values(["Stack value", "Item"], ascending=[False, True], kind="stable").reset_index(drop=True)

    st.caption(
        "Each item gets a plan automatically. Change **Plan** to override it (remembered next run, and it "
        "applies to every copy of that item). **List price** is editable for plain items; weapon/armor "
        "copies are re-priced from their rolls by the autofill script."
    )
    edited = st.data_editor(
        df,
        width="stretch",
        hide_index=True,
        key=_EDITOR_KEY,
        column_order=("Plan", "Item", "Qty", "List price", "Est. proceeds", "Why", "Floor", "Fair", "Shop pays"),
        column_config={
            "Plan": st.column_config.SelectboxColumn(options=[PLAN_LABELS[p] for p in PLANS], required=True),
            "Item": st.column_config.Column(disabled=True),
            "Qty": st.column_config.NumberColumn(format="%,.0f", disabled=True),
            "List price": st.column_config.NumberColumn(min_value=1, step=1, format="%,.0f"),
            "Est. proceeds": st.column_config.NumberColumn(format="%,.0f", disabled=True),
            "Why": st.column_config.Column(disabled=True),
            "Floor": st.column_config.NumberColumn(format="%,.0f", disabled=True),
            "Fair": st.column_config.NumberColumn(format="%,.0f", disabled=True),
            "Shop pays": st.column_config.NumberColumn(format="%,.0f", disabled=True),
        },
    )

    new_overrides = overrides_from_edit(df, edited, overrides)
    if new_overrides != overrides or rules.to_dict() != SellRules.from_dict(saved_rules).to_dict():
        save_sell_prefs(rules.to_dict(), new_overrides)
        if new_overrides != overrides:
            st.rerun()

    _render_totals(edited)
    _render_shop_and_keep(edited, profiles)

    return {
        "item_plans": item_plans(an["df_summary"], edited, profiles, rules, new_overrides),
        "rules": rules,
    }


def _row_proceeds(row) -> float:
    plan = LABEL_TO_PLAN.get(row["Plan"])
    price = _num(row.get("List price"))
    if plan == "keep" or not price:
        return 0.0
    if plan == "shop":
        return float(price) * row["Qty"]
    return float(price) * row["Qty"] * (1.0 - MARKET_FEE)


def _render_totals(edited: pd.DataFrame) -> None:
    by_plan = edited.assign(_p=edited.apply(_row_proceeds, axis=1)).groupby("Plan")["_p"].agg(["count", "sum"])
    cols = st.columns(len(PLANS))
    for col, plan in zip(cols, PLANS):
        label = PLAN_LABELS[plan]
        n, total = (int(by_plan.loc[label, "count"]), by_plan.loc[label, "sum"]) if label in by_plan.index else (0, 0.0)
        col.metric(f"{label} ({n})", f"${total:,.0f}" if plan != "keep" else "—")


def _render_shop_and_keep(edited: pd.DataFrame, profiles: dict) -> None:
    shop = edited[edited["Plan"] == PLAN_LABELS["shop"]]
    if not shop.empty:
        st.markdown("**Sell to a city shop** (no listing needed)")
        st.dataframe(
            pd.DataFrame(
                {
                    "Item": shop["Item"],
                    "Qty": shop["Qty"],
                    "Shop pays": shop["Shop pays"],
                    "Sold in": [(profiles.get(str(i)) or {}).get("vendor") or "—" for i in shop["item_id"]],
                }
            ),
            width="stretch",
            hide_index=True,
        )
    st.caption("The Autofill script lists Dump / Competitive / Top dollar items and leaves Shop and Keep rows blank.")
