"""Where to fly: rank destinations by the profit you can expect to carry home,
using the foreign stock history the logger collects (foreign_stock.py).

The odds come from the history itself: for each item, every past moment its
country was observed with the item in the same state as now (sold out, fewer
than a full load, or a full load or more) is a sample, and what was in stock
one flight later is the outcome. Joint draws over those outcomes give the
expected load: the most profitable item first, topped up with the next best
if it has run short.
"""

from __future__ import annotations

import csv
import json
import random
import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from .foreign_stock import COUNTRIES
from .my_listings import market_listings
from .sell_plan import MARKET_FEE

# One-way flight, standard class, in minutes; other classes scale it.
FLIGHT_MIN = {
    "mex": 26, "cay": 35, "can": 41, "haw": 134, "uni": 159, "arg": 167,
    "swi": 175, "jap": 225, "chi": 242, "uae": 271, "sou": 297,
}
TRAVEL_SPEED = {"Standard": 1.0, "Airstrip": 0.7, "Private jet": 0.5, "Business class": 0.3}
MATCH_S = 15 * 60  # an observation this close to the landing time stands in for it
MIN_SAMPLES = 10  # fewer than this and the odds fall back to "any state now"
CHOICES = 5  # items per country considered for the load, best first


@dataclass
class History:
    obs: dict[str, list[int]] = field(default_factory=dict)  # country -> sorted observation times
    changes: dict[tuple[str, int], tuple[list[int], list[int]]] = field(default_factory=dict)  # -> (times, qtys)
    names: dict[int, str] = field(default_factory=dict)
    cost: dict[tuple[str, int], int] = field(default_factory=dict)  # latest

    @property
    def span_h(self) -> float:
        times = [t for ts in self.obs.values() for t in ts]
        return (max(times) - min(times)) / 3600 if times else 0.0


def load_history(stock_path: Path, updates_path: Path) -> History:
    h = History()
    for r in _read(updates_path):
        h.obs.setdefault(r["country"], []).append(int(r["seen"]))
    for ts in h.obs.values():
        ts.sort()
    rows = sorted(_read(stock_path), key=lambda r: int(r["seen"]))
    for r in rows:
        key = (r["country"], int(r["item_id"]))
        times, qtys = h.changes.setdefault(key, ([], []))
        times.append(int(r["seen"]))
        qtys.append(int(r["quantity"]))
        h.names[key[1]] = r["item_name"]
        h.cost[key] = int(r["cost"])
    return h


def qty_at(times: list[int], qtys: list[int], t: int) -> Optional[int]:
    i = bisect_right(times, t) - 1
    return qtys[i] if i >= 0 else None


def state(qty: int, cap: int) -> int:
    return 0 if qty <= 0 else (1 if qty < cap else 2)


def arrival_samples(h: History, key: tuple[str, int], horizon_s: float, cap: int, now_state: Optional[int]) -> list[int]:
    """Stock seen about `horizon_s` after each past observation in which the
    item was in `now_state` (any state if None)."""
    obs = h.obs.get(key[0]) or []
    times, qtys = h.changes[key]
    out = []
    for t in obs:
        q0 = qty_at(times, qtys, t)
        if q0 is None or (now_state is not None and state(q0, cap) != now_state):
            continue
        target = t + horizon_s
        j = bisect_left(obs, target)
        near = min((obs[k] for k in (j - 1, j) if 0 <= k < len(obs)), key=lambda s: abs(s - target))
        if abs(near - target) > MATCH_S:
            continue  # nobody looked around then (or it hasn't happened yet)
        out.append(qty_at(times, qtys, near))
    return out


def sell_prices(market_rows: dict[int, dict], ref_prices: dict[int, float]) -> dict[int, float]:
    """What each item fetches after tax: $1 under the cheapest listing, never
    above market value (what it has actually been selling for)."""
    out = {}
    for iid, row in market_rows.items():
        listings = market_listings(row or {})
        ref = ref_prices.get(iid) or 0
        if listings:
            floor = min(l["price"] for l in listings) - 1
            sell = min(floor, ref) if ref > 0 else floor
        elif ref > 0:
            sell = ref
        else:
            continue
        out[iid] = sell * (1.0 - MARKET_FEE)
    return out


def write_prices(path: Path, sell: dict[int, float], now: Optional[float] = None) -> None:
    """Save sell prices for the userscript, which picks what to buy on the shop page abroad."""
    data = {"checked": int(now or time.time()), "sell": {str(k): round(v) for k, v in sell.items()}}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def candidates(h: History, ref_prices: dict[int, float]) -> list[int]:
    """Items worth a live price check: profitable at market value somewhere."""
    return sorted({iid for (c, iid), cost in h.cost.items()
                   if cost > 0 and (ref_prices.get(iid) or 0) * (1.0 - MARKET_FEE) > cost})


def plan_trips(h: History, now: float, sell: dict[int, float], cap: int = 18, speed: float = 0.5,
               draws: int = 2000, seed: int = 0) -> pd.DataFrame:
    """One row per destination with something profitable, best per hour first."""
    rng = random.Random(seed)
    rows = []
    for country, obs in h.obs.items():
        if country not in FLIGHT_MIN or not obs:
            continue
        flight_s = FLIGHT_MIN[country] * 60 * speed
        horizon = max(0.0, now - obs[-1]) + flight_s
        options = []
        for key in [k for k in h.changes if k[0] == country]:
            profit = sell.get(key[1], 0) - h.cost.get(key, 0)
            if profit <= 0:
                continue
            now_qty = h.changes[key][1][-1]
            samples = arrival_samples(h, key, horizon, cap, state(now_qty, cap))
            basis = "like now"
            if len(samples) < MIN_SAMPLES:
                samples, basis = arrival_samples(h, key, horizon, cap, None), "any time"
            if not samples:
                samples, basis = [now_qty], "no history"
            options.append({"id": key[1], "profit": profit, "now": now_qty, "samples": samples, "basis": basis})
        if not options:
            continue
        options.sort(key=lambda o: o["profit"], reverse=True)
        options = options[:CHOICES]
        total = 0.0
        for _ in range(draws):
            left, value = cap, 0.0
            for o in options:
                n = min(rng.choice(o["samples"]), left)
                value += n * o["profit"]
                left -= n
                if not left:
                    break
            total += value
        expected = total / draws
        best = options[0]
        round_trip_h = 2 * flight_s / 3600
        rows.append({
            "Country": COUNTRIES.get(country, country),
            "Flight (min)": round(flight_s / 60),
            "Best item": h.names.get(best["id"]),
            "Profit / unit": round(best["profit"]),
            "In stock now": best["now"],
            "Chance of a full load": round(100.0 * sum(q >= cap for q in best["samples"]) / len(best["samples"])),
            "Expected trip profit": round(expected),
            "Per hour": round(expected / round_trip_h),
            "Backups": ", ".join(f"{h.names.get(o['id'])} ({o['now']})" for o in options[1:]),
            "Odds from": f"{len(best['samples'])} samples, {best['basis']}",
            "Data age (min)": round(max(0.0, now - obs[-1]) / 60),
            "best_id": best["id"],
            "code": country,
        })
    df = pd.DataFrame(rows)
    return df.sort_values("Per hour", ascending=False).reset_index(drop=True) if not df.empty else df


def _read(path: Path) -> list[dict]:
    try:
        with Path(path).open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []
