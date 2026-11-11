"""Append-only price history: one CSV row per priced item per run.

Owned items are recorded for free (they're priced anyway); `track` categories
add every tradable item in them, owned or not, at one market call each.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .inventory import inventory_category_for_profile

COLUMNS = (
    "time", "item_id", "item_name", "category", "floor", "fast", "fair", "greedy",
    "listings", "units_listed", "my_qty", "ref_price",
)


def tracked_item_ids(profiles: dict, categories: Iterable[str], exclude: Iterable[int] = ()) -> list[int]:
    """Every tradable item (reference price > 0) in `categories`, minus `exclude`."""
    cats, skip = set(categories), {int(i) for i in exclude}
    out = []
    for pid, prof in profiles.items():
        if int(pid) in skip or not (prof.get("market_price") or 0) > 0:
            continue
        if inventory_category_for_profile(prof) in cats:
            out.append(int(pid))
    return sorted(out)


def history_rows(df_summary: pd.DataFrame, df_long: pd.DataFrame, profiles: dict, now: float) -> list[dict]:
    units = df_long.groupby("item_id")["quantity"].sum() if not df_long.empty else pd.Series(dtype=float)
    # Local time, as the person running the app reads it.
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for r in df_summary.to_dict("records"):
        iid = int(r["item_id"])
        prof = profiles.get(str(iid)) or {}
        rows.append({
            "time": stamp,
            "item_id": iid,
            "item_name": r.get("item_name") or prof.get("name"),
            "category": inventory_category_for_profile(prof) or prof.get("type") or "",
            "floor": _num(r.get("floor_price")),
            "fast": _num(r.get("fast_sell_price")),
            "fair": _num(r.get("fair_price")),
            "greedy": _num(r.get("greedy_price")),
            "listings": int(r.get("num_listings") or 0),
            "units_listed": int(units.get(iid, 0)),
            "my_qty": int(r.get("my_quantity") or 0),
            "ref_price": _num(prof.get("market_price")),
        })
    return rows


def append_history(path: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return len(rows)


def load_history(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, ValueError):
        return pd.DataFrame(columns=COLUMNS)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 2)
