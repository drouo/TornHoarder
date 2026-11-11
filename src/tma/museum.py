"""Museum sets: is a complete set worth more exchanged for points than sold
piece by piece on the Item Market?

torn/museum lists each set's items and points; market/pointsmarket gives the
current cheapest point price. Exchange value = points x point price; selling
value = each piece's fast-sell price after the market tax.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

from .fmt import money
from .http_client import BASE_URL, get_json_with_retry
from .sell_plan import MARKET_FEE

MAX_AGE_S = 24 * 3600


def parse_museum(data: dict) -> list[dict]:
    return [
        {"name": s["name"], "points": int(s["points"]), "items": [int(i) for i in s["items"]]}
        for s in (data or {}).get("museum") or []
        if s.get("items") and s.get("points")
    ]


def refresh_museum_if_stale(
    session: requests.Session, api_key: str, path: Path, max_age_s: float = MAX_AGE_S
) -> tuple[bool, Optional[str]]:
    """Re-download the museum set list when missing or older than a day. Keeps the old copy on error."""
    try:
        if time.time() - Path(path).stat().st_mtime < max_age_s:
            return False, None
    except OSError:
        pass
    data, err = get_json_with_retry(session, None, f"{BASE_URL}/torn/museum", api_key, timeout=20, retries=2)
    if data is None:
        return False, err
    sets = parse_museum(data)
    if not sets:
        return False, "torn/museum returned no sets"
    Path(path).write_text(json.dumps({"fetched": int(time.time()), "sets": sets}, indent=2), encoding="utf-8")
    return True, None


def load_museum(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("sets") or []
    except (OSError, ValueError, AttributeError):
        return []


def fetch_point_price(session: requests.Session, api_key: str) -> tuple[Optional[float], Optional[str]]:
    """Cheapest current points-market price per point."""
    data, err = get_json_with_retry(session, None, f"{BASE_URL}/market/pointsmarket", api_key, timeout=20, retries=2)
    if data is None:
        return None, err
    costs = [float(l["cost"]) for l in (data.get("pointsmarket") or []) if l.get("cost")]
    return (min(costs), None) if costs else (None, "market/pointsmarket returned no listings")


def evaluate_sets(
    sets: list[dict],
    owned: dict[int, int],
    unit_value: dict[int, float],
    ref_price: dict[int, float],
    names: dict[int, str],
    point_price: Optional[float],
) -> list[dict]:
    """One row per set you own at least one piece of.

    `unit_value` is each piece's fast-sell price; pieces without one are
    valued at Torn's reference price. `missing` lists what the next set
    still needs, with its cost at reference prices.
    """
    out = []
    for s in sets:
        items = s["items"]
        counts = [int(owned.get(i, 0)) for i in items]
        if not any(counts):
            continue
        complete = min(counts)
        sell_one = sum(float(unit_value.get(i) or ref_price.get(i) or 0.0) for i in items) * (1.0 - MARKET_FEE)
        exchange_one = s["points"] * point_price if point_price else None
        short = [i for i, c in zip(items, counts) if c <= complete]
        out.append({
            "set": s["name"],
            "points": s["points"],
            "pieces": len(items),
            "complete": complete,
            "exchange_value": exchange_one,
            "sell_value": sell_one,
            "better": None if exchange_one is None else ("Exchange" if exchange_one > sell_one else "Sell"),
            "gain_per_set": None if exchange_one is None else abs(exchange_one - sell_one),
            "missing": [names.get(i) or f"#{i}" for i in short],
            "missing_cost": sum(float(ref_price.get(i) or 0.0) for i in short),
            "items": items,
        })
    return out


def museum_holds(evaluations: list[dict]) -> dict[int, tuple[int, str]]:
    """Pieces of complete sets worth more exchanged: keep one per complete set."""
    holds: dict[int, tuple[int, str]] = {}
    for e in evaluations:
        if e["better"] != "Exchange" or not e["complete"]:
            continue
        why = (
            f"museum: {e['complete']} {e['set']} set(s) worth {money(e['exchange_value'])} each exchanged "
            f"vs {money(e['sell_value'])} sold"
        )
        for iid in e["items"]:
            n, _ = holds.get(iid, (0, ""))
            holds[iid] = (n + e["complete"], why) if n else (e["complete"], why)
    return holds
