"""Fresh prices on landing: while you fly abroad, re-check the destination's
items a few minutes before you land, so the shop page abroad ranks by prices
minutes old rather than whenever Plan a trip was last clicked.

The userscript says when you're in the air (POST /flying to the local
server); this reads where to and when you land (Torn's user/travel), waits
until LEAD_S before landing, then checks that country's items on the Item
Market and the traders' price lists, and updates those items in
travel_prices.json (the rest are kept as they were)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .foreign_stock import COUNTRIES
from .travel_plan import History, candidates, sell_prices, with_traders

LEAD_S = 3 * 60  # check this long before landing
LOOKUP_GAP_S = 60  # at most one travel lookup a minute, however many pages say you're flying
ALIASES = {"united arab emirates": "uae"}


def country_code(name: Optional[str]) -> Optional[str]:
    name = (name or "").strip().lower()
    for code, full in COUNTRIES.items():
        if full.lower() == name:
            return code
    return ALIASES.get(name)


def parse_travel(data: Optional[dict]) -> Optional[tuple[str, int]]:
    """(country code, landing time) while flying abroad; None at home, flying
    back to Torn, or already landed."""
    t = (data or {}).get("travel") or {}
    code = country_code(t.get("destination"))
    try:
        arrival, left = int(t.get("arrival_at") or 0), int(t.get("time_left") or 0)
    except (TypeError, ValueError):
        return None
    return (code, arrival) if code and arrival and left > 0 else None


def destination_ids(h: History, code: str, refs: dict[int, float], offers: dict[int, dict]) -> list[int]:
    """Items sold in `code` worth a price check (see travel_plan.candidates)."""
    sold_there = {iid for (c, iid) in h.cost if c == code}
    return [i for i in candidates(h, refs, offers) if i in sold_there]


def merge_prices(path: Path, sell: dict[int, float], via: dict[int, str], ids: list[int], now: float) -> None:
    """Replaces `ids` in the saved sell prices (and who pays), keeping every other item."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    old_sell, old_via = data.get("sell") or {}, data.get("via") or {}
    for i in ids:
        old_sell.pop(str(i), None)
        old_via.pop(str(i), None)
    old_sell.update({str(k): round(v) for k, v in sell.items()})
    old_via.update({str(k): v for k, v in via.items()})
    data.update({"checked": int(now), "sell": old_sell})
    if old_via:
        data["via"] = old_via
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def check_destination(
    code: str, h: History, refs: dict[int, float], offers: dict[int, dict],
    fetch_markets: Callable[[list[int]], dict[int, dict]], prices_path: Path, now: Optional[float] = None,
) -> str:
    """Checks `code`'s items and saves their prices; returns a line for the log."""
    ids = destination_ids(h, code, refs, offers)
    if not ids:
        return f"nothing in {COUNTRIES.get(code, code)} worth a price check"
    rows = fetch_markets(ids)
    sell, via = with_traders(sell_prices(rows, refs), {i: offers[i] for i in ids if i in offers})
    sell = {i: v for i, v in sell.items() if i in ids}
    merge_prices(prices_path, sell, {i: v for i, v in via.items() if i in ids}, ids, time.time() if now is None else now)
    best = sorted(((v - h.cost.get((code, i), 0), i) for i, v in sell.items()), reverse=True)[:3]
    return (f"checked {len(rows)} of {len(ids)} items in {COUNTRIES.get(code, code)}; best: " +
            ", ".join(f"{h.names.get(i, i)} +${p:,.0f}" + (f" to {via[i]}" if i in via else "") for p, i in best))


class PreLanding:
    """Schedules one check per flight, LEAD_S before landing."""

    def __init__(self, lookup: Callable[[], tuple[Optional[dict], Optional[str]]], check: Callable[[str], str],
                 log: Callable[[str], None], clock: Callable[[], float] = time.time,
                 timer: Callable[..., threading.Timer] = threading.Timer):
        self.lookup, self.check, self.log, self.clock, self.timer = lookup, check, log, clock, timer
        self.lock = threading.Lock()
        self.looked_at = 0.0
        self.arrival = 0  # the flight already scheduled

    def flying(self) -> None:
        """The userscript saw you in the air: schedule this flight's check once."""
        with self.lock:
            now = self.clock()
            if now - self.looked_at < LOOKUP_GAP_S or self.arrival > now:
                return
            self.looked_at = now
        data, err = self.lookup()
        got = parse_travel(data)
        if not got:
            self.log(f"pre-landing: no flight abroad to check ({err or json.dumps((data or {}).get('travel'))[:300]})")
            return
        code, arrival = got
        with self.lock:
            if arrival == self.arrival:
                return
            self.arrival = arrival
        delay = max(0.0, arrival - LEAD_S - self.clock())
        self.log(f"pre-landing: flying to {COUNTRIES[code]}, landing in {round((arrival - self.clock()) / 60)} min; "
                 f"prices checked in {round(delay / 60)} min")
        t = self.timer(delay, self._run, args=(code,))
        t.daemon = True
        t.start()

    def _run(self, code: str) -> None:
        try:
            self.log("pre-landing: " + self.check(code))
        except Exception as exc:  # a background thread: say what broke instead of dying silently
            self.log(f"pre-landing: check for {COUNTRIES.get(code, code)} failed: {type(exc).__name__}: {exc}")
