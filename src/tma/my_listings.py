"""Your active Item Market listings (user/itemmarket) checked against the
current market: who undercut you, and what to re-price to.

A listing is matched against the market's other listings for the same
item; your own listings are taken out of the market first (without_mine),
so nothing you list or re-price ever undercuts another of yours.
Weapon/armor copies only compete with copies of equal or better quality.
"""

from __future__ import annotations

from typing import Optional

import requests

from .fmt import money
from .http_client import BASE_URL, TokenBucket, get_json_with_retry
from .sell_plan import price_guards

MAX_PAGES = 20
# Cheapest by more than this vs the next listing: you could ask more.
GAP_PCT = 10.0


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
    item = mine.get("item") or {}
    price = float(mine["price"])
    quality = ((item.get("stats") or {}).get("quality")) if item.get("uid") else None
    others = list(market)  # already without your listings (without_mine)
    if item.get("uid"):
        others = [l for l in others if l.get("quality") is not None and quality is not None and l["quality"] >= quality]
    others.sort(key=lambda l: l["price"])
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
    if not others:
        row["Status"] = "only listing" + (" of this quality or better" if item.get("uid") else "")
        return row
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
    return row
