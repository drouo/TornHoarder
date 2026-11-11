"""Profile card (avatar, level, status, bars, money) and loadout at the top of the app."""

from __future__ import annotations

from html import escape
from typing import Optional

import pandas as pd
import streamlit as st

from tma.fmt import money
from tma.profile import SLOTS, item_image_url, total_money

_STATUS_COLORS = {"green": "#43a047", "red": "#e53935", "blue": "#1e88e5"}


def _bar(label: str, bar: Optional[dict], color: str) -> str:
    if not bar:
        return ""
    cur, mx = int(bar.get("current") or 0), int(bar.get("maximum") or 0)
    pct = min(cur / mx * 100.0, 100.0) if mx else 0.0
    return (
        f'<div style="font-size:12px;margin:2px 0 0;">{label} {cur:,}/{mx:,}</div>'
        f'<div style="background:#3333;border-radius:3px;height:6px;"><div style="width:{pct:.0f}%;'
        f'background:{color};height:6px;border-radius:3px;"></div></div>'
    )


def render_profile_card(bundle: dict, point_price: Optional[float] = None) -> None:
    prof = bundle.get("profile")
    if not prof:
        if bundle.get("errors"):
            st.caption("Couldn't load your profile: " + "; ".join(bundle["errors"]))
        return
    status = prof.get("status") or {}
    color = _STATUS_COLORS.get(status.get("color"), "#888")
    bars = bundle.get("bars") or {}
    img = prof.get("image")
    left, right = st.columns([1, 3])
    with left:
        if img:
            st.markdown(f'<img src="{escape(img)}" style="width:100%;max-width:150px;border-radius:6px;">', unsafe_allow_html=True)
    with right:
        st.markdown(
            f'<div style="font-size:1.15rem;"><b>{escape(prof.get("name") or "")}</b> [{prof.get("id")}] · '
            f'Level {prof.get("level")} · {escape(prof.get("rank") or "")}</div>'
            f'<div style="font-size:13px;color:{color};">● {escape(status.get("description") or "")}'
            f'<span style="color:#888;"> · last action {escape((prof.get("last_action") or {}).get("relative") or "")}</span></div>'
            + _bar("Life", bars.get("life") or prof.get("life"), "#43a047")
            + _bar("Energy", bars.get("energy"), "#7cb342")
            + _bar("Nerve", bars.get("nerve"), "#e53935")
            + _bar("Happy", bars.get("happy"), "#fdd835"),
            unsafe_allow_html=True,
        )
        funds = total_money(bundle.get("money"), point_price)
        if funds["cash"]:
            parts = [f"Wallet **{money(funds['cash'].get('wallet', 0))}**"]
            banked = funds["cash_total"] - funds["cash"].get("wallet", 0)
            if banked:
                parts.append(f"banked/other {money(banked)}")
            pts = f"{funds['points']:,} points" + (f" (≈{money(funds['points_value'])})" if funds["points_value"] else "")
            parts.append(pts)
            if funds["networth"]:
                parts.append(f"networth {money(funds['networth'])}")
            # Escaped: two bare $ in one markdown line render as a math formula.
            st.markdown(" · ".join(parts).replace("$", "\\$"))


def loadout_rows(equipment: list[dict], values: Optional[dict] = None) -> list[dict]:
    values = values or {}
    order = list(SLOTS)
    rows = []
    for e in sorted(equipment or [], key=lambda e: order.index(e.get("slot")) if e.get("slot") in order else 99):
        stats = e.get("stats") or {}
        bonuses = ", ".join(f"{b.get('title')} {b.get('value')}%" for b in e.get("bonuses") or [])
        rows.append({
            "Image": item_image_url(e["id"]),
            "Slot": SLOTS.get(e.get("slot"), e.get("type") or ""),
            "Item": e.get("name"),
            "Damage": stats.get("damage"),
            "Accuracy": stats.get("accuracy"),
            "Armor": stats.get("armor"),
            "Quality": stats.get("quality"),
            "Bonuses": bonuses,
            "Worth today": values.get(str(e.get("uid") or e["id"])),
        })
    return rows


def render_loadout(equipment: Optional[list[dict]], values: Optional[dict] = None) -> None:
    if not equipment:
        return
    rows = loadout_rows(equipment, values)
    with st.expander(f"Loadout ({len(rows)} equipped)"):
        df = pd.DataFrame(rows)
        if df["Worth today"].isna().all():
            df = df.drop(columns=["Worth today"])
            st.caption("Click **Run** to price your loadout on today's market.")
        else:
            total = df["Worth today"].fillna(0).sum()
            st.caption(f"Worth about **{money(total)}** on today's market (copies priced at their quality).")
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "Image": st.column_config.ImageColumn("", width="small"),
                "Worth today": st.column_config.NumberColumn(format="compact"),
            },
        )
