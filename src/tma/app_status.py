"""What has been run, how long ago, and what to do next: the app's status
board. Everything comes from files the tools already write, so it survives
app restarts.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .shop_flips import day_start

FRESH, STALE, NEVER = "fresh", "stale", "never"
ICON = {FRESH: "✅", STALE: "⚠️", NEVER: "○"}

SELL_STALE_S = 6 * 3600
STOCK_STALE_S = 2 * 3600
TRAVEL_STALE_S = 30 * 60
LOGGER_STALE_S = 15 * 60


@dataclass
class Job:
    name: str
    section: str  # where its controls are: Sell, Restock or Travel
    state: str  # FRESH, STALE or NEVER
    detail: str  # what the last run found
    todo: str  # what to do now; empty when nothing needs doing
    when: Optional[float] = None


def ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    s = max(0, int(now - ts))
    if s < 90:
        return "just now"
    if s < 90 * 60:
        return f"{round(s / 60)} min ago"
    if s < 36 * 3600:
        return f"{round(s / 3600)}h ago"
    return f"{round(s / 86400)} days ago"


def write_last_run(path: Path, items: int, source: str, now: Optional[float] = None) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"checked": int(now or time.time()), "items": items, "source": source}),
                          encoding="utf-8")


def _json(path: Path) -> dict:
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def script_version(path: Path) -> Optional[str]:
    try:
        head = Path(path).read_text(encoding="utf-8")[:2000]
    except OSError:
        return None
    m = re.search(r"@version\s+(\S+)", head)
    return m.group(1) if m else None


def last_csv_time(path: Path) -> Optional[int]:
    """The first column of the last row of a CSV, read from the file's tail."""
    try:
        with Path(path).open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = f.read().decode("utf-8", "ignore").strip().splitlines()
        return int(lines[-1].split(",")[0]) if lines else None
    except (OSError, ValueError):
        return None


def jobs(now: float, *, last_run: Path, script: Path, app_version: str, stock_status: Path, has_minimums: bool,
         shop_plan: Path, travel_prices: Path, foreign_updates: Path) -> list[Job]:
    out = []

    run = _json(last_run)
    t = run.get("checked")
    if not t:
        out.append(Job("Sell prices", "Sell", NEVER, "No run yet.", "Click Run to price your items."))
    else:
        old = now - t > SELL_STALE_S
        out.append(Job("Sell prices", "Sell", STALE if old else FRESH,
                       f"{run.get('items', 0)} items priced {ago(t, now)}.",
                       "Prices are over 6h old: Run again before you list." if old else "", t))

    v = script_version(script)
    if not v:
        out.append(Job("Torn script", "Sell", NEVER, "Not built yet.", "Run once, then install it from the sidebar."))
    elif v != app_version:
        out.append(Job("Torn script", "Sell", STALE, f"Built as v{v}; the app is v{app_version}.",
                       "Run to rebuild it, then update it from the sidebar."))
    else:
        out.append(Job("Torn script", "Sell", FRESH, f"v{v}, built with the latest prices.", ""))

    st = _json(stock_status)
    t = st.get("checked")
    short = len(st.get("short") or [])
    if not has_minimums:
        out.append(Job("Keep stocked", "Restock", NEVER, "No minimums set.", "Set the items you always want on hand."))
    elif not t:
        out.append(Job("Keep stocked", "Restock", NEVER, "Not checked yet.", "Open Restock to check your stock."))
    else:
        old = now - t > STOCK_STALE_S
        out.append(Job("Keep stocked", "Restock", STALE if old or short else FRESH,
                       (f"{short} item(s) below minimum" if short else "Everything stocked") + f", checked {ago(t, now)}.",
                       "Restock all from the Restock section." if short else ("Check now." if old else ""), t))

    plan = _json(shop_plan)
    t = plan.get("checked")
    n = len(plan.get("route") or [])
    if not t:
        out.append(Job("City shop buys", "Restock", NEVER, "No scan yet.", "Scan shops for today's 100 buys."))
    elif t < day_start(now):
        out.append(Job("City shop buys", "Restock", STALE, f"Last scan {ago(t, now)}, before today's reset.",
                       "Scan shops for today's 100 buys.", t))
    else:
        out.append(Job("City shop buys", "Restock", FRESH, f"{n} shop(s) planned, scanned {ago(t, now)}.", "", t))

    t = _json(travel_prices).get("checked")
    if not t:
        out.append(Job("Where to fly", "Travel", NEVER, "No prices yet.", "Plan a trip before you fly."))
    else:
        old = now - t > TRAVEL_STALE_S
        out.append(Job("Where to fly", "Travel", STALE if old else FRESH, f"Prices checked {ago(t, now)}.",
                       "Plan a trip again before you fly." if old else "", t))

    t = last_csv_time(foreign_updates)
    if not t:
        out.append(Job("Foreign stock logger", "Travel", NEVER, "No stock logged yet.",
                       "Start it: python tools/log_foreign_stock.py"))
    else:
        old = now - t > LOGGER_STALE_S
        out.append(Job("Foreign stock logger", "Travel", STALE if old else FRESH, f"Last update {ago(t, now)}.",
                       "It may have stopped: python tools/log_foreign_stock.py" if old else "", t))
    return out
