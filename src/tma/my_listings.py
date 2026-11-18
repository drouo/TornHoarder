"""Your active Item Market listings (user/itemmarket) checked against the
current market: who undercut you, and what to re-price to.

A listing is matched against the market's other listings for the same
item; your own listings are taken out of the market first (without_mine),
so nothing you list or re-price ever undercuts another of yours.
Weapon/armor copies only compete with copies of equal or better quality.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

from .fmt import money
from .http_client import BASE_URL, TokenBucket, get_json_with_retry
from .market_enrichment import LOW_OUTLIER_FACTOR, few_cheap
from .sell_plan import price_guards

MAX_PAGES = 20
# Cheapest by more than this vs the next listing: you could ask more.
GAP_PCT = 10.0
# A suggestion this far under your price, or under this share of Torn's
# market value, is flagged: it never fills without your tick.
BIG_DROP_PCT = 25.0
REF_WARN = 0.8


def fetch_my_listings(
    session: requests.Session, api_key: str, bucket: Optional[TokenBucket] = None
) -> tuple[list[dict], Optional[str]]:
    out: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        data, err = get_json_with_retry(
            session, bucket, f"{BASE_URL}/user/itemmarket", api_key, params={"offset": offset}, timeout=20, retries=2
        )
        if data is None:
            return out, err
        page = data.get("itemmarket") or []
        out.extend(page)
        if not page or not ((data.get("_metadata") or {}).get("links") or {}).get("next"):
            break
        offset += len(page)
    return out, None


def drop_listed_copies(entries: list, listed: list[dict]) -> list:
    """Inventory entries minus weapon/armor copies that are on the market
    (matched by uid). Stacks can't be matched one by one; listing them already
    takes them out of the inventory count."""
    uids = {str((l.get("item") or {}).get("uid")) for l in listed if (l.get("item") or {}).get("uid")}
    return [e for e in entries if not (e.uid and str(e.uid) in uids)]


LISTING_FIELDS = ("price", "amount", "quality", "damage", "accuracy", "armor", "uid")


def without_mine(wide_row: dict, mine: list[dict]) -> dict:
    """A market_fetch wide row minus your own listings: copies matched by uid,
    stacks by price and amount (one market listing per listing of yours).
    Everything priced from the market starts from this, so your listings are
    never something to undercut."""
    iid = wide_row.get("item_id")
    own = [l for l in mine if (l.get("item") or {}).get("id") == iid]
    if not own:
        return wide_row
    uids = {str(l["item"]["uid"]) for l in own if l["item"].get("uid")}
    stacks = [(float(l["price"]), l.get("amount")) for l in own if not l["item"].get("uid")]
    kept = []
    i = 1
    while f"price_{i}" in wide_row:
        lst = {f: wide_row.get(f"{f}_{i}") for f in LISTING_FIELDS}
        i += 1
        if lst["price"] is None or lst["price"] != lst["price"]:
            continue
        if lst["uid"] and str(lst["uid"]) in uids:
            continue
        key = (float(lst["price"]), lst["amount"])
        if not lst["uid"] and key in stacks:
            stacks.remove(key)
            continue
        kept.append(lst)
    out = {k: v for k, v in wide_row.items() if not any(k.startswith(f + "_") and k[len(f) + 1:].isdigit() for f in LISTING_FIELDS)}
    for n in range(1, i):
        lst = kept[n - 1] if n <= len(kept) else {}
        for f in LISTING_FIELDS:
            out[f"{f}_{n}"] = lst.get(f)
    return out


def market_listings(wide_row: dict) -> list[dict]:
    """[{price, amount, quality}] from a market_fetch wide row (see without_mine)."""
    out = []
    i = 1
    while f"price_{i}" in wide_row:
        p = wide_row.get(f"price_{i}")
        if p is not None and p == p:
            out.append({"price": float(p), "amount": wide_row.get(f"amount_{i}"), "quality": wide_row.get(f"quality_{i}")})
        i += 1
    return out


def review_listing(
    mine: dict,
    market: list[dict],
    shop_price: Optional[float] = None,
    ref_price: Optional[float] = None,
) -> dict:
    """One listing against the others: Status says what's going on, Why what
    the suggested price rests on, and Big drop flags a suggestion that cuts
    deep (your call, never a one-click fill)."""
    item = mine.get("item") or {}
    price = float(mine["price"])
    quality = ((item.get("stats") or {}).get("quality")) if item.get("uid") else None
    others = list(market)  # already without your listings (without_mine)
    if item.get("uid"):
        others = [l for l in others if l.get("quality") is not None and quality is not None and l["quality"] >= quality]
    others.sort(key=lambda l: l["price"])
    # A dumper far under the rest isn't the price to match (as for selling).
    dumped = []
    if len(others) >= 3:
        mid = others[len(others) // 2]["price"]
        dumped = [l for l in others if l["price"] < LOW_OUTLIER_FACTOR * mid]
        if few_cheap(len(dumped), len(others)):
            others = others[len(dumped):]
        else:
            dumped = []  # that many cheap listings are the market, not dumpers
    shop_min, ref_min = price_guards(shop_price, ref_price)
    floor = max(shop_min or 0, ref_min or 0) or None

    row = {
        "listing_id": mine.get("id"),
        "item_id": item.get("id"),
        "Item": item.get("name"),
        "uid": item.get("uid"),
        "Quality": quality,
        "Amount": mine.get("amount"),
        "Your price": price,
        "Cheapest other": others[0]["price"] if others else None,
        "Cheaper listings": sum(1 for l in others if l["price"] < price),
        "New price": None,
        "Status": "",
    }
    def usd(v: float) -> str:  # exact: in a reason $1,359 and $1,360 differ
        return f"${v:,.0f}"

    why = [f"{len(others)} other listing(s)" + (f" at quality {quality:.0f}+" if quality is not None else "")]
    if others:
        why.append(f"cheapest {usd(others[0]['price'])}")
    if dumped:
        why.append(f"ignored {len(dumped)} dumped under {usd(LOW_OUTLIER_FACTOR * mid)}")
    if floor:
        why.append(f"never under {usd(floor)} (" + " / ".join(
            n for n, v in (("shop pays", shop_min), ("half market value", ref_min)) if v and v == floor) + ")")
    row["Why"] = " · ".join(why)

    if not others:
        row["Status"] = "only listing" + (" of this quality or better" if item.get("uid") else "")
    else:
        best = others[0]["price"]
        if best < price:
            target = best - 1
            if floor and target < floor:
                row["Status"] = f"undercut by {money(price - best)}; matching it would go under your floor {money(floor)}"
            else:
                row["Status"] = f"undercut by {money(price - best)}"
                row["New price"] = target
        elif best == price:
            row["Status"] = "tied for cheapest"
            if not floor or price - 1 >= floor:
                row["New price"] = price - 1
        else:
            gap = (best - price) / best * 100.0
            if gap > GAP_PCT:
                row["Status"] = f"cheapest by {gap:.0f}%: could ask up to {money(best - 1)}"
                row["New price"] = best - 1
            else:
                row["Status"] = "cheapest"
    new = row["New price"]
    row["Big drop"] = bool(new is not None and (new < price * (1 - BIG_DROP_PCT / 100.0) or
                                                (ref_price and new < REF_WARN * ref_price)))
    return row


def listing_plan(rows: list[dict]) -> list[dict]:
    """The re-prices for the userscript's panel on your listings page."""
    return [{"item_id": r["item_id"], "uid": str(r["uid"]) if r.get("uid") else None, "name": r["Item"],
             "quality": r.get("Quality"), "now": r["Your price"], "new": r["New price"], "why": r.get("Why", ""),
             "status": r.get("Status", ""), "big": bool(r.get("Big drop"))}
            for r in rows if r.get("New price") is not None and r.get("item_id")]


def write_listing_plan(path: Path, rows: list[dict], now: Optional[float] = None) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"checked": int(time.time() if now is None else now), "rows": listing_plan(rows)}),
                          encoding="utf-8")
