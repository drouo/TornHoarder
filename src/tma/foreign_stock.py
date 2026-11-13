"""Foreign shop stock history, from YATA's public travel feed.

Torn's API has no stock data for shops abroad; YATA crowdsources it from
travellers' userscripts. The feed only says what's in stock *now*, so this
logs it over time to learn how likely an item is to still be there when you
land:

- data/foreign_stock.csv: one row per (country, item) whenever its quantity
  or cost changes, stamped with YATA's observation time.
- data/foreign_updates.csv: one row per new observation of a country, so
  "nothing changed" can be told apart from "nobody looked".

Between two rows an item's stock is taken as unchanged, which holds as long
as the country was observed in between (foreign_updates.csv says when).
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Callable, Optional

import requests

YATA_URL = "https://yata.yt/api/v1/travel/export/"
COUNTRIES = {
    "mex": "Mexico", "cay": "Cayman Islands", "can": "Canada", "haw": "Hawaii", "uni": "United Kingdom",
    "arg": "Argentina", "swi": "Switzerland", "jap": "Japan", "chi": "China", "uae": "UAE", "sou": "South Africa",
}
STOCK_COLUMNS = ("seen", "country", "item_id", "item_name", "quantity", "cost")
UPDATE_COLUMNS = ("seen", "country")
DEFAULT_INTERVAL_S = 300  # YATA is a volunteer service: every 5 minutes is plenty


def fetch_yata(session: requests.Session) -> tuple[dict, Optional[str]]:
    """{country code: {"update": epoch, "stocks": [...]}} or ({}, error)."""
    try:
        resp = session.get(YATA_URL, timeout=30)
        resp.raise_for_status()
        return resp.json().get("stocks") or {}, None
    except (requests.RequestException, ValueError) as exc:
        return {}, f"YATA: {exc}"


def observations(stocks: dict) -> list[dict]:
    """Flatten the feed: one dict per (country, item) as last observed."""
    rows = []
    for code, country in stocks.items():
        seen = int(country.get("update") or 0)
        for it in country.get("stocks") or []:
            rows.append({
                "seen": seen, "country": code, "item_id": int(it["id"]), "item_name": it.get("name"),
                "quantity": int(it.get("quantity") or 0), "cost": int(it.get("cost") or 0),
            })
    return rows


class StockLog:
    """Appends only what changed since the last row for each (country, item),
    and each new country observation. Resumes from the files on disk, so a
    restart doesn't log everything again."""

    def __init__(self, stock_path: Path, updates_path: Path) -> None:
        self.stock_path, self.updates_path = Path(stock_path), Path(updates_path)
        self.last: dict[tuple[str, int], tuple[int, int]] = {}
        self.seen: dict[str, int] = {}
        for r in _read(self.stock_path):
            self.last[(r["country"], int(r["item_id"]))] = (int(r["quantity"]), int(r["cost"]))
        for r in _read(self.updates_path):
            self.seen[r["country"]] = max(self.seen.get(r["country"], 0), int(r["seen"]))

    def record(self, stocks: dict) -> tuple[int, int]:
        """(stock rows written, country observations written)."""
        changed, updates = [], []
        for code, country in stocks.items():
            seen = int(country.get("update") or 0)
            if seen and seen > self.seen.get(code, 0):
                self.seen[code] = seen
                updates.append({"seen": seen, "country": code})
        for row in observations(stocks):
            key = (row["country"], row["item_id"])
            if self.last.get(key) != (row["quantity"], row["cost"]):
                self.last[key] = (row["quantity"], row["cost"])
                changed.append(row)
        _append(self.stock_path, STOCK_COLUMNS, changed)
        _append(self.updates_path, UPDATE_COLUMNS, updates)
        return len(changed), len(updates)


def run(log: StockLog, session: requests.Session, interval_s: int = DEFAULT_INTERVAL_S,
        once: bool = False, say: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> None:
    while True:
        stocks, err = fetch_yata(session)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if err:
            say(f"{stamp} {err}")
        else:
            n, u = log.record(stocks)
            say(f"{stamp} {n} stock change(s), {u} new country observation(s)")
        if once:
            return
        sleep(interval_s)


def _read(path: Path) -> list[dict]:
    try:
        with Path(path).open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _append(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    if not rows:
        return
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        if new:
            w.writeheader()
        w.writerows({k: r[k] for k in columns} for r in rows)
