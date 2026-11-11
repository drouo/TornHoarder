"""Sell plan tab: every item gets an automatic plan (Dump / Competitive /
Top dollar / Shop / Keep) with a list price to match. Changing an item's
Plan is remembered across runs (~/.tma_config.json); the resulting prices
feed the autofill script.

Replaces the old Bulk sell tab, whose flat "% of Fair price" was either
still above the floor (never sells) or far below it (gives money away).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from dataclasses import replace

from tma.config import load_reserves, load_sell_prefs, save_reserves, save_sell_prefs
from tma.crime_items import load_oc_items
from tma.inventory import inventory_category_for_profile
from tma.museum import evaluate_sets, museum_holds
from tma.sell_plan import (
    KEEP_ALL,
    LABEL_TO_PLAN,
    MARKET_FEE,
    PLAN_LABELS,
    PLANS,
    Decision,
    SellRules,
    decide,
    price_guards,
    reserve_for,
)
from tma.shops import build_shop_run, load_shop_knowledge, shop_price_for

from ..paths import OC_ITEMS_PATH, SHOP_BUYS_PATH

_EDITOR_KEY = "sell_plan_editor"


def _num(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


# Add Listing page tab ("View <label>") for each user/inventory category.
# Category tabs list every weapon/armor copy on its own row ("View All" folds
# copies into groups), so the userscript fills tab by tab.
LISTING_TABS = {
    "Primary": "Primary", "Secondary": "Secondary", "Melee": "Melee", "Temporary": "Temporary",
    "Defensive": "Armor", "Clothing": "Clothing", "Medical": "Medical", "Drug": "Drugs",
    "Energy Drink": "Energy Drinks", "Alcohol": "Alcohol", "Candy": "Candy", "Supply Pack": "Supply Packs",
    "Booster": "Boosters", "Enhancer": "Enhancers", "Tool": "Tools", "Material": "Materials",
    "Jewelry": "Jewelry", "Flower": "Flowers", "Plushie": "Plushies", "Car": "Cars",
    "Artifact": "Artifacts", "Special": "Special", "Other": "Miscellaneous", "Collectible": "Collectibles",
}


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
    known: Optional[dict] = None,
    oc_items: Optional[dict] = None,
    reserves: Optional[dict[int, int]] = None,
    museum: Optional[dict[int, tuple[int, str]]] = None,
) -> list[dict]:
    """One row per pricing-table row (stackables combined, one per weapon/armor copy).

    Reserves come first: a stack sells only what's above its keep number,
    and for weapon/armor the best copies (by quality) are the ones kept.
    Museum holds (pieces of complete sets worth more exchanged) add to the
    automatic reserve; a number typed into Keeping still wins.
    """
    reserves = reserves or {}
    museum = museum or {}
    keep_by_item: dict[int, tuple[int, str]] = {}
    for ro in rows_out:
        iid = int(ro["item_id"])
        if iid not in keep_by_item:
            prof = profiles.get(str(iid)) or {}
            keep_by_item[iid] = reserve_for(
                iid, prof, inventory_category_for_profile(prof), rules, oc_items, reserves.get(iid)
            )
            if iid not in reserves and iid in museum and museum[iid][0] > keep_by_item[iid][0]:
                keep_by_item[iid] = museum[iid]
    kept_copies: set[int] = set()
    for iid, (n, _) in keep_by_item.items():
        copies = [i for i, ro in enumerate(rows_out) if int(ro["item_id"]) == iid and ro.get("is_stat")]
        copies.sort(key=lambda i: real_q_by_idx.get(i) if real_q_by_idx.get(i) is not None else float("-inf"), reverse=True)
        kept_copies.update(copies[:n])

    out = []
    for idx, ro in enumerate(rows_out):
        iid = int(ro["item_id"])
        prof = profiles.get(str(iid)) or {}
        summ = summary_by_id.get(iid) or {}
        qty = int(_num(ro.get("My quantity")) or 0)
        reserve, keep_why = keep_by_item[iid]
        keeping = (1 if idx in kept_copies else 0) if ro.get("is_stat") else min(reserve, qty)
        selling = qty - keeping
        fast = _num(ro.get("Fast-sell price"))
        if ro.get("is_stat") and _num(summ.get("fast_sell_price")):
            # A copy's quality-curve price can rest on one cheap listing in a thin
            # band; no copy is worth less than the item's cheapest listing.
            fast = max(fast or 0, _num(summ.get("fast_sell_price")))
        kwargs = dict(
            qty=selling,
            fast=fast,
            fair=_num(ro.get("Fair price")),
            greedy=_num(ro.get("Greedy price")),
            shop_price=shop_price_for(prof, iid, known),
            num_listings=_num(summ.get("num_clean_listings")),
            quality=real_q_by_idx.get(idx) if ro.get("is_stat") else None,
            rules=rules,
            ref_price=prof.get("market_price"),
            sellable=is_sellable(prof, rules),
        )
        auto: Decision = decide(**kwargs)
        d: Decision = decide(**kwargs, override=overrides.get(iid)) if iid in overrides else auto
        why = d.reason
        if keeping and not selling:
            if ro.get("is_stat"):
                what = "every copy" if reserve >= KEEP_ALL else "your best copy"
            else:
                what = f"all {qty:,}"
            auto = d = Decision("keep", f"keeping {what}: {keep_why}", None, 0.0, True)
            why = d.reason
        elif keeping:
            why = f"{d.reason} · keeping {keeping:,} ({keep_why})"
        out.append(
            {
                "item_id": iid,
                "is_stat": bool(ro.get("is_stat")),
                "auto_plan": auto.plan,
                "Plan": PLAN_LABELS[d.plan],
                "Item": ro["Item"],
                "Qty": qty,
                "Keeping": keeping,
                "Selling": selling,
                "quality": real_q_by_idx.get(idx) if ro.get("is_stat") else None,
                "List price": d.price,
                "Why": why,
                "Floor": _num(summ.get("floor_price")),
                "Fair": kwargs["fair"],
                "Shop pays": shop_price_for(prof, iid, known),
                "Est. proceeds": int(round(d.proceeds)),
                "Stack value": float(kwargs["fair"] or kwargs["fast"] or 0.0) * qty,
                "uid": ro.get("uid"),
            }
        )
    return out


def item_plans(
    df_summary: pd.DataFrame,
    edited: pd.DataFrame,
    profiles: dict,
    rules: SellRules,
    overrides: dict[int, str],
    known: Optional[dict] = None,
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
        shop_price = shop_price_for(prof, iid, known)
        kwargs = dict(
            qty=int(_num(r.get("my_quantity")) or 0),
            fast=_num(r.get("fast_sell_price")),
            fair=_num(r.get("fair_price")),
            greedy=_num(r.get("greedy_price")),
            num_listings=_num(r.get("num_clean_listings")),
            quality=None,
            rules=rules,
            # Weapon/armor: the cheapest junk copy vs Torn's all-quality average
            # would hold every copy; the script applies refMin per copy instead.
            ref_price=None if iid in stat_ids else prof.get("market_price"),
            sellable=is_sellable(prof, rules),
        )
        d = decide(**kwargs, shop_price=shop_price, override=overrides.get(iid))
        shop_min, ref_min = price_guards(shop_price, prof.get("market_price"))
        plans[iid] = {
            "tab": LISTING_TABS.get(inventory_category_for_profile(prof)),
            "plan": d.plan,
            "auto": d.auto,
            "list": d.price,
            "shopMin": shop_min,
            # A plan you picked yourself is listed anyway (the app shows the warning).
            "refMin": ref_min if d.auto else None,
        }
        if d.plan == "shop":
            # Still in inventory on the Add Listing page means no shop took it
            # (do the shop run first): list it with its market plan instead.
            fb = decide(**kwargs, shop_price=None, override=None if overrides.get(iid) == "shop" else overrides.get(iid))
            plans[iid]["fallback"] = {"plan": fb.plan, "list": fb.price, "refMin": ref_min if fb.auto else None}
    for iid, rows in edited.groupby("item_id"):
        iid = int(iid)
        if iid not in plans:
            continue
        entry = plans[iid]
        if not bool(rows["is_stat"].iloc[0]):
            row = rows.iloc[0]
            if entry["plan"] != "shop":
                price = _num(row.get("List price"))
                entry["list"] = int(price) if price else None
            # Only the surplus is ever listed or sold; the reserve stays put.
            entry["qty"] = int(row["Selling"])
            if not entry["qty"]:
                entry["plan"] = "keep"
            continue
        # For the Add Listing review: how many copies are up for sale and
        # roughly what they'll list for (the script re-prices each from its rolls).
        selling = rows[rows["Keeping"] == 0]
        entry["copies"] = int(len(selling))
        entry["estimate"] = int(sum(_num(p) or 0 for p in selling["List price"]))
        kept = rows[rows["Keeping"] > 0]
        if len(kept) == len(rows):
            entry["plan"] = "keep"
        elif len(kept):
            # The Add Listing page shows rolls, not copy ids: hold every row at
            # least as good as the worst copy we're keeping (unknown -> hold all).
            qualities = [_num(q) for q in kept["quality"]]
            entry["keepMinQuality"] = None if None in qualities else float(min(qualities))
            entry["keepAll"] = None in qualities
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
        st.markdown("**Always keep** (only the surplus is ever sold)")
        keep_crime = st.checkbox(
            "Keep all crime items (Organized Crime requirements and anything a crime uses)",
            value=bool(base.keep_all_crime_items),
            key="rule_keep_crime",
        )
        st.caption("Untick to keep only a stock of each and sell the rest:")
        k1, k2, k3 = st.columns(3)
        with k1:
            oc_tool = st.number_input("OC tools (reusable)", 0, 50, int(base.oc_tool_keep), 1, key="rule_oc_tool")
        with k2:
            oc_used = st.number_input("OC items used up per run", 0, 50, int(base.oc_consumable_keep), 1, key="rule_oc_used")
        with k3:
            hint = st.number_input("Other crime items", 0, 50, int(base.crime_hint_keep), 1, key="rule_crime_hint")
        keep_one = st.multiselect(
            "Keep 1 of every item in",
            sorted(set(base.sell_categories) | set(base.keep_one_categories)),
            default=list(base.keep_one_categories),
            key="rule_keep_one",
        )
        st.caption(
            f"Market proceeds are after Torn's {MARKET_FEE:.0%} item-market sales tax. Dump/Competitive items switch "
            "to **Shop** automatically when a city shop pays at least that much. OC items come from Torn's "
            "organized-crimes list; other crime items from their in-game description. Type a number in "
            "**Keeping** to set any item by hand."
        )
    return replace(
        base,
        dump_undercut_pct=float(undercut),
        competitive_min_unit=float(unit),
        competitive_min_stack=float(stack),
        top_min_quality=float(quality),
        thin_market_listings=int(thin),
        keep_one_categories=tuple(keep_one),
        keep_all_crime_items=bool(keep_crime),
        oc_tool_keep=int(oc_tool),
        oc_consumable_keep=int(oc_used),
        crime_hint_keep=int(hint),
    )


def render_sell_plan(an: dict, ctx: dict) -> dict:
    saved_rules, overrides = load_sell_prefs()
    rules = _render_rules(saved_rules)

    summary_by_id = {int(r["item_id"]): r for r in an["df_summary"].to_dict("records")}
    profiles = an.get("profiles") or {}
    known = load_shop_knowledge(SHOP_BUYS_PATH)
    oc_items = load_oc_items(OC_ITEMS_PATH)
    reserves = load_reserves()
    museum_sets = museum_table(ctx["rows_out"], summary_by_id, profiles, an.get("museum") or {})
    rows = plan_rows(
        ctx["rows_out"], ctx["real_q_by_idx"], summary_by_id, profiles, rules, overrides, known, oc_items, reserves,
        museum_holds(museum_sets),
    )
    # data_editor stores edits by row position, so the order must not depend
    # on the plan (which edits change) — stack value doesn't.
    df = pd.DataFrame(rows)
    df = df.sort_values(["Stack value", "Item"], ascending=[False, True], kind="stable").reset_index(drop=True)

    st.caption(
        "Each item gets a plan automatically. Change **Plan** to override it, or type how many to keep in "
        "**Keeping** (both remembered next run, for every copy of that item). Only **Selling** is ever listed or "
        "sold. **List price** is editable for plain items; weapon/armor copies are re-priced from their rolls."
    )
    edited = st.data_editor(
        df,
        width="stretch",
        hide_index=True,
        key=_EDITOR_KEY,
        column_order=(
            "Plan", "Item", "Qty", "Keeping", "Selling", "List price", "Est. proceeds", "Why", "Floor", "Fair", "Shop pays"
        ),
        column_config={
            "Plan": st.column_config.SelectboxColumn(options=[PLAN_LABELS[p] for p in PLANS], required=True),
            "Item": st.column_config.Column(disabled=True),
            "Qty": st.column_config.NumberColumn(format="localized", disabled=True),
            "Keeping": st.column_config.NumberColumn(min_value=0, step=1, format="localized", help="How many you keep. Type a number to set it by hand."),
            "Selling": st.column_config.NumberColumn(format="localized", disabled=True),
            "List price": st.column_config.NumberColumn(min_value=1, step=1, format="localized"),
            "Est. proceeds": st.column_config.NumberColumn(format="localized", disabled=True),
            "Why": st.column_config.Column(disabled=True),
            "Floor": st.column_config.NumberColumn(format="localized", disabled=True),
            "Fair": st.column_config.NumberColumn(format="localized", disabled=True),
            "Shop pays": st.column_config.NumberColumn(format="localized", disabled=True),
        },
    )

    new_overrides = overrides_from_edit(df, edited, overrides)
    new_reserves = reserves_from_edit(df, edited, reserves)
    if new_reserves != reserves:
        save_reserves(new_reserves)
    if new_overrides != overrides or rules.to_dict() != SellRules.from_dict(saved_rules).to_dict():
        save_sell_prefs(rules.to_dict(), new_overrides)
    if new_overrides != overrides or new_reserves != reserves:
        st.rerun()

    _render_totals(edited)
    _render_money(an.get("money"), float(edited.apply(_row_proceeds, axis=1).sum()))
    shop_run = build_shop_run(shop_rows(edited), profiles, known)
    _render_shop_run(shop_run)
    _render_market_run(edited)
    _render_museum(museum_sets, (an.get("museum") or {}).get("point_price"))
    _render_my_listings(an.get("my_listings") or [])

    return {
        "item_plans": item_plans(an["df_summary"], edited, profiles, rules, new_overrides, known),
        "rules": rules,
        "shop_run": shop_run,
    }


def shop_rows(edited: pd.DataFrame) -> list[dict]:
    shop = edited[(edited["Plan"] == PLAN_LABELS["shop"]) & (edited["Selling"] > 0)]
    return [
        {
            "item_id": r["item_id"],
            "name": r["Item"],
            "qty": r["Selling"],
            "uid": _num(r.get("uid")),
            "is_stat": r["is_stat"],
            "price": _num(r["List price"]),
        }
        for _, r in shop.iterrows()
    ]


def reserves_from_edit(base: pd.DataFrame, edited: pd.DataFrame, reserves: dict[int, int]) -> dict[int, int]:
    """Per-item keep numbers typed into the Keeping column (weapon/armor: copies kept across rows)."""
    new = dict(reserves)
    for iid in edited.loc[edited["Keeping"] != base["Keeping"], "item_id"].unique():
        rows = edited[edited["item_id"] == iid]
        new[int(iid)] = int(rows["Keeping"].clip(lower=0).sum()) if rows["is_stat"].iloc[0] else int(max(rows["Keeping"].iloc[0], 0))
    return new


def _row_proceeds(row) -> float:
    plan = LABEL_TO_PLAN.get(row["Plan"])
    price = _num(row.get("List price"))
    if plan == "keep" or not price:
        return 0.0
    if plan == "shop":
        return float(price) * row["Selling"]
    return float(price) * row["Selling"] * (1.0 - MARKET_FEE)


def _render_totals(edited: pd.DataFrame) -> None:
    by_plan = edited.assign(_p=edited.apply(_row_proceeds, axis=1)).groupby("Plan")["_p"].agg(["count", "sum"])
    cols = st.columns(len(PLANS))
    for col, plan in zip(cols, PLANS):
        label = PLAN_LABELS[plan]
        n, total = (int(by_plan.loc[label, "count"]), by_plan.loc[label, "sum"]) if label in by_plan.index else (0, 0.0)
        col.metric(f"{label} ({n})", f"${total:,.0f}" if plan != "keep" else "—")


def _render_shop_run(shop_run: dict) -> None:
    route = shop_run["route"]
    if not route:
        st.caption("Nothing to sell to a city shop.")
        return
    total = sum(s["payout"] for s in route)
    n = sum(s["items"] for s in route)
    st.markdown(f"**Shop run**: {n:,} items · ${total:,.0f} across {len(route)} shop(s)")
    st.dataframe(
        pd.DataFrame([{"Shop": s["name"], "Items": s["items"], "Payout": s["payout"]} for s in route]),
        width="stretch",
        hide_index=True,
        column_config={"Payout": st.column_config.NumberColumn(format="localized")},
    )
    st.markdown(f"[**Start shop run →**]({route[0]['url']})")
    st.caption(
        "Needs the autofill script from this run (Autofill script tab). On each shop page one button sits in the "
        "same spot: click it to sell this shop's items, then again for the next shop. Only Shop-plan items are "
        "filled; weapon/armor copies are matched one by one, so a high-roll copy is never sold to a shop."
    )


ADD_LISTING_URL = "https://www.torn.com/page.php?sid=ItemMarket#/addListing"


def _render_market_run(edited: pd.DataFrame) -> None:
    market = edited[edited["Plan"].isin([PLAN_LABELS[p] for p in ("dump", "competitive", "top")]) & (edited["Selling"] > 0)]
    if market.empty:
        st.caption("Nothing to list on the Item Market.")
        return
    units = int(market["Selling"].sum())
    total = float(market.apply(_row_proceeds, axis=1).sum())
    st.markdown(
        f"**Market listings**: {units:,} items ({market['item_id'].nunique()} kinds) · about ${total:,.0f} after the "
        f"{MARKET_FEE:.0%} tax"
    )
    st.markdown(f"[**Open Add Listing →**]({ADD_LISTING_URL})")
    st.caption(
        "Do the shop run first. On Add Listing, the green button shows the planned listings (click a line to leave it "
        "out), then **Fill** goes tab by tab filling quantity and price for every planned row (weapon/armor priced from "
        "each copy's rolls; Keep, crime items and your best copies are left alone). Check the list, then **Add** presses "
        "Torn's ADD TO MARKET. Nothing is listed until you click."
    )


def museum_table(rows_out: list[dict], summary_by_id: dict[int, dict], profiles: dict, museum: dict) -> list[dict]:
    owned: dict[int, int] = {}
    for ro in rows_out:
        iid = int(ro["item_id"])
        owned[iid] = owned.get(iid, 0) + int(_num(ro.get("My quantity")) or 0)
    unit = {iid: _num(r.get("fast_sell_price")) for iid, r in summary_by_id.items()}
    ref = {int(k): p.get("market_price") for k, p in profiles.items()}
    names = {int(k): p.get("name") for k, p in profiles.items()}
    return evaluate_sets(museum.get("sets") or [], owned, unit, ref, names, museum.get("point_price"))


def _render_museum(sets: list[dict], point_price) -> None:
    if not sets:
        return
    if not point_price:
        st.caption("Museum sets: couldn't read the points market this run, so sets can't be compared.")
        return
    st.markdown(f"**Museum sets** · points at ${point_price:,.0f} each")
    st.dataframe(
        pd.DataFrame([
            {
                "Set": e["set"],
                "Complete": e["complete"],
                "Exchanged": e["exchange_value"],
                "Sold": e["sell_value"],
                "Better": e["better"],
                "Gain / set": e["gain_per_set"],
                "Next set needs": ", ".join(e["missing"]) if len(e["missing"]) <= 3 else f"{len(e['missing'])} pieces",
                "Cost (ref)": e["missing_cost"],
            }
            for e in sorted(sets, key=lambda e: (-e["complete"], len(e["missing"])))
        ]),
        width="stretch",
        hide_index=True,
        column_config={c: st.column_config.NumberColumn(format="localized") for c in ("Exchanged", "Sold", "Gain / set", "Cost (ref)")},
    )
    st.caption(
        "**Exchanged** is the set's points at the cheapest points-market price; **Sold** is every piece at its "
        f"fast-sell price after the {MARKET_FEE:.0%} tax (Torn's reference price for pieces you don't own). When "
        "exchanging is better, one of each piece per complete set is kept out of the plan automatically; trade "
        "them in at the museum. **Next set needs** is what one more set is missing, at reference prices."
    )


VIEW_LISTINGS_URL = "https://www.torn.com/page.php?sid=ItemMarket#/viewListing"


def _render_my_listings(rows: list[dict]) -> None:
    if not rows:
        st.caption("No active Item Market listings.")
        return
    df = pd.DataFrame(rows)
    n_fix = int(df["New price"].notna().sum())
    st.markdown(f"**Your listings**: {len(df)} active · {n_fix} worth re-pricing")
    st.dataframe(
        df.sort_values(["New price", "Item"], na_position="last"),
        width="stretch",
        hide_index=True,
        column_order=("Item", "Quality", "Amount", "Your price", "Cheapest other", "Cheaper listings", "New price", "Status"),
        column_config={c: st.column_config.NumberColumn(format="localized") for c in ("Amount", "Your price", "Cheapest other", "New price")},
    )
    st.markdown(f"[**Open your listings →**]({VIEW_LISTINGS_URL})")
    st.caption(
        "Checked against the market this run: your own listing is left out, and weapon/armor copies only compete "
        "with copies of the same quality or better. **New price** is $1 under the cheapest other listing, never under "
        "what a shop pays or half Torn's reference price; when you're cheapest by a wide margin it's what you could ask."
    )


def _render_money(money: Optional[dict], proceeds: float) -> None:
    if not money or not money.get("cash"):
        return
    wallet = money["cash"].get("wallet", 0.0)
    text = (
        f"Wallet now ${wallet:,.0f} · this plan adds about ${proceeds:,.0f} → **${wallet + proceeds:,.0f}** "
        f"(everything: ${money['cash_total'] + proceeds:,.0f} cash"
        + (f", networth ${money['networth']:,.0f} today" if money.get("networth") else "")
        + ")."
    )
    # Escaped: two bare $ in one markdown line render as a math formula.
    st.caption(text.replace("$", "\\$"))
