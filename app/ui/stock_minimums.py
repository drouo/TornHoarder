"""Keep stocked: set minimums, see what's short, and buy it back up. The
check also feeds the userscript's in-Torn badge (via /stock-status)."""

from __future__ import annotations

import json
from typing import Callable

import pandas as pd
import streamlit as st

from tma.config import load_minimums, save_minimums
from tma.fmt import money
from tma.http_client import session_for_requests, shared_bucket
from tma.inventory import fetch_inventory
from tma.item_stats import load_profiles
from tma.stock_minimums import AMMO_SLOTS, RESTOCK_URL, categories_for, check_stock, restock_route, write_status

from .. import paths

AMMO_LABELS = {"ammo:primary": "Primary ammo (equipped gun, Standard)", "ammo:secondary": "Secondary ammo (equipped gun, Standard)"}


@st.cache_data(ttl=300, show_spinner=False)
def _held(api_key: str, categories: tuple[str, ...]) -> tuple[dict[int, int], list[str]]:
    """{item_id: quantity held, not equipped} across `categories`; re-read at most every 5 minutes."""
    bucket = shared_bucket()
    inv = fetch_inventory(session_for_requests(), api_key, categories=categories, bucket_take=bucket.take)
    held: dict[int, int] = {}
    for e in inv.entries:
        if not e.equipped:
            held[e.item_id] = held.get(e.item_id, 0) + int(e.amount)
    return held, inv.errors


def render_keep_stocked(api_key: str, bundle_fn: Callable[[str], dict]) -> None:
    minimums = load_minimums()
    profiles = load_profiles(paths.PROFILES_PATH)
    rows = []
    if api_key and minimums:
        held, errors = _held(api_key, categories_for(minimums, profiles))
        rows = check_stock(minimums, held, (bundle_fn(api_key) or {}).get("equipment"), profiles)
        write_status(paths.STOCK_STATUS_PATH, rows)
    short = [r for r in rows if r["need"] > 0]
    title = f"Keep stocked: ⚠ {len(short)} below minimum" if short else "Keep stocked"
    with st.expander(title, expanded=bool(short)):
        st.caption(
            "Items you always want on hand. Anything below its minimum shows here and as a badge on every Torn "
            "page, with a link to buy the difference where it's cheaper (city shop or Item Market, never over 10% "
            "above market value). The sell plan never sells below a minimum. Stock is re-read every 5 minutes."
        )
        if short:
            lines = []
            for r in short:
                where = f" · [{r['source']}: buy {r['need']:,} →]({r['url']})" if r["url"] else ""
                each = f" at about {money(r['unit'])} each" if r.get("unit") else ""
                lines.append(f"**{r['name']}**: {r['have']:,} of {r['min']:,} (need {r['need']:,}{each}){where}")
            st.markdown("  \n".join(lines).replace("$", "\\$"))
            st.markdown(f"[**Restock all: {len(restock_route(rows))} stop(s) →**]({RESTOCK_URL})")
            st.caption(
                "Opens Torn and walks through every stop, like the shop run: each page fills what to buy, you click Buy "
                "and confirm, and the button then goes on to the next stop. The badge on Torn has the same link."
            )
        elif rows:
            st.success("Everything is at or above its minimum.")
        if rows and errors:
            st.caption("Some inventory couldn't be read: " + "; ".join(errors))
        if st.button("Check now", key="stock_check_now", disabled=not (api_key and minimums)):
            _held.clear()
            bundle_fn.clear()
            st.rerun()
        _edit_minimums(minimums, profiles)


def _edit_minimums(minimums: dict[str, int], profiles: dict) -> None:
    names = {f"item:{k}": p.get("name") for k, p in profiles.items() if p.get("name")}
    labels = {**AMMO_LABELS, **{k: f"{n} [{k[5:]}]" for k, n in names.items()}}
    by_label = {v: k for k, v in labels.items()}
    current = pd.DataFrame(
        [{"Item": labels.get(k, k), "Minimum": v} for k, v in minimums.items()] or [{"Item": None, "Minimum": None}]
    )
    edited = st.data_editor(
        current, key="stock_minimums_editor", num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "Item": st.column_config.SelectboxColumn(
                options=list(AMMO_LABELS.values()) + sorted(v for k, v in labels.items() if k not in AMMO_SLOTS),
                help="Pick an item, or Primary/Secondary ammo for whatever gun is equipped there.",
            ),
            "Minimum": st.column_config.NumberColumn(min_value=0, step=10, format="localized"),
        },
    )
    new = {}
    for r in edited.to_dict("records"):
        key = by_label.get(r.get("Item"))
        if key and pd.notna(r.get("Minimum")) and int(r["Minimum"]) > 0:
            new[key] = int(r["Minimum"])
    if new != minimums and st.button("Save minimums", key="stock_minimums_save", type="primary"):
        save_minimums(new)
        _held.clear()
        st.rerun()


def status_json() -> str:
    """The latest check as JSON, baked into a newly built userscript as a fallback."""
    try:
        return paths.STOCK_STATUS_PATH.read_text(encoding="utf-8")
    except OSError:
        return json.dumps({"checked": 0, "short": []})
