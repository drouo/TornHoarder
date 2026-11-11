"""Your active Item Market listings (user/itemmarket) checked against the
current market: who undercut you, and what to re-price to.

A listing is matched against the market's other listings for the same
item (one listing at your price is assumed to be yours and left out).
Weapon/armor copies only compete with copies of equal or better quality.
"""

from __future__ import annotations

from typing import Optional

import requests

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


def market_listings(wide_row: dict) -> list[dict]:
    """[{price, amount, quality}] from a market_fetch wide row."""
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
    others = list(market)
    for i, l in enumerate(others):
        if l["price"] == price:
            del others[i]
            break
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
            row["Status"] = f"undercut by ${price - best:,.0f}; matching it would go under your floor ${floor:,.0f}"
        else:
            row["Status"] = f"undercut by ${price - best:,.0f}"
            row["New price"] = target
    elif best == price:
        row["Status"] = "tied for cheapest"
        if not floor or price - 1 >= floor:
            row["New price"] = price - 1
    else:
        gap = (best - price) / best * 100.0
        if gap > GAP_PCT:
            row["Status"] = f"cheapest by {gap:.0f}%: could ask up to ${best - 1:,.0f}"
            row["New price"] = best - 1
        else:
            row["Status"] = "cheapest"
    return row
