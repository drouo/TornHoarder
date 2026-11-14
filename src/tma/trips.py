"""What the userscript bought abroad, for a per-trip profit log.

The shop page abroad posts each buy it sees land in the bag (`POST
/trip-buy`); data/trip_buys.csv keeps them. Consecutive buys in one country
make a trip. "Expected" values use the sell price the app had when you
bought; "now" uses the latest Plan a trip prices.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import pandas as pd

from .foreign_stock import COUNTRIES

COLUMNS = ("ts", "country", "item_id", "item_name", "qty", "cost", "sell")
TRIP_GAP_S = 3 * 3600  # buys further apart than this are separate trips


def record_buy(path: Path, rec: dict) -> None:
    """Append one buy. Raises ValueError/TypeError on a malformed record."""
    row = {
        "ts": int(rec["ts"]), "country": str(rec["country"])[:32], "item_id": int(rec["item_id"]),
        "item_name": str(rec.get("item_name") or "")[:80], "qty": int(rec["qty"]),
        "cost": int(rec["cost"]), "sell": int(rec.get("sell") or 0),
    }
    if row["qty"] <= 0 or row["cost"] < 0:
        raise ValueError("bad buy")
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)


def load_buys(path: Path) -> list[dict]:
    try:
        with Path(path).open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    out = []
    for r in rows:
        try:
            out.append({k: (r[k] if k in ("country", "item_name") else int(r[k])) for k in COLUMNS})
        except (KeyError, ValueError):
            continue
    return sorted(out, key=lambda r: r["ts"])


def trip_table(buys: list[dict], sell_now: Optional[dict[int, float]] = None) -> pd.DataFrame:
    """One row per trip, newest first."""
    trips: list[list[dict]] = []
    for b in buys:
        last = trips[-1][-1] if trips else None
        if last and last["country"] == b["country"] and b["ts"] - last["ts"] <= TRIP_GAP_S:
            trips[-1].append(b)
        else:
            trips.append([b])
    rows = []
    for t in trips:
        items: dict[str, int] = {}
        for b in t:
            items[b["item_name"]] = items.get(b["item_name"], 0) + b["qty"]
        spent = sum(b["qty"] * b["cost"] for b in t)
        expected = sum(b["qty"] * b["sell"] for b in t)
        row = {
            "When": pd.Timestamp(t[0]["ts"], unit="s", tz="UTC").tz_convert(None).strftime("%Y-%m-%d %H:%M"),
            "Country": COUNTRIES.get(t[0]["country"], t[0]["country"].replace("-", " ").title()),
            "Bought": ", ".join(f"{q} × {n}" for n, q in items.items()),
            "Spent": spent,
            "Expected profit": expected - spent,
        }
        if sell_now is not None:
            have = all(int(b["item_id"]) in sell_now for b in t)
            row["Profit at today's prices"] = (
                sum(b["qty"] * sell_now[int(b["item_id"])] for b in t) - spent if have else None)
        rows.append(row)
    return pd.DataFrame(rows[::-1])
