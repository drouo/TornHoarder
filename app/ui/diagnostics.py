"""Diagnostics tab: raw/intermediate tables useful for debugging a run."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from tma.inventory import load_stats_cache
from tma.item_stats import load_profiles

from .. import paths

_VIEWS = (
    "Parsed items", "Unmatched items", "Market data (wide)", "Market data (long)", "Pricing diagnostics",
    "Copy stats cache",
)


def stats_cache_table(cache: dict, profiles: dict, now: float) -> pd.DataFrame:
    """One row per cached weapon/armor copy, longest-held first. Copies cached
    before first-seen tracking have no date."""
    rows = []
    for uid, c in cache.items():
        if not c:
            continue
        iid = c.get("item_id")
        seen = c.get("first_seen")
        rows.append({
            "Item": (profiles.get(str(iid)) or {}).get("name") or (f"#{iid}" if iid else "?"),
            "uid": str(uid),
            "Quality": c.get("quality"),
            "Damage": c.get("damage"),
            "Accuracy": c.get("accuracy"),
            "Armor": c.get("armor"),
            "First seen": pd.to_datetime(seen, unit="s").strftime("%Y-%m-%d %H:%M") if seen else None,
            "Days held": round((now - seen) / 86400, 1) if seen else None,
        })
    df = pd.DataFrame(rows, columns=["Item", "uid", "Quality", "Damage", "Accuracy", "Armor", "First seen", "Days held"])
    return df.sort_values(["Days held", "Item"], ascending=[False, True], na_position="last").reset_index(drop=True)


def render_diagnostics(an: dict, df_summary: pd.DataFrame) -> None:
    view = st.radio("Table", _VIEWS, horizontal=True, key="diagnostics_view")

    if view == "Parsed items":
        st.dataframe(an["df_parsed"], width="stretch", hide_index=True)
    elif view == "Unmatched items":
        if an["unmatched"]:
            df_unmatched = (
                pd.DataFrame(an["unmatched"], columns=["name", "qty"])
                .sort_values("name")
                .reset_index(drop=True)
            )
            st.dataframe(df_unmatched, width="stretch", hide_index=True)
        else:
            st.info("No unmatched items.")
    elif view == "Market data (wide)":
        st.dataframe(an["df_market"], width="stretch", hide_index=True)
    elif view == "Market data (long)":
        st.dataframe(an["df_long"], width="stretch", hide_index=True)
    elif view == "Copy stats cache":
        df = stats_cache_table(load_stats_cache(paths.UID_STATS_PATH), load_profiles(paths.PROFILES_PATH), time.time())
        st.caption(
            f"{len(df)} weapon/armor copies whose rolls are remembered (`data/uid_stats.json`), so later runs "
            "skip reading them from Torn. Copies you no longer own are dropped on each full inventory run. "
            "*First seen* is when TornHoarder first read the copy (blank: cached before tracking started)."
        )
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.dataframe(df_summary, width="stretch", hide_index=True)
