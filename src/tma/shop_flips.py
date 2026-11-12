"""City-shop flips: what Torn's city shops have in stock right now that resells
on the Item Market for more than it costs (after the 5% sales tax).

Stock comes from torn/cityshops; each scan is also logged to a CSV so
restock patterns can be learned later.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from .http_client import BASE_URL, TokenBucket, get_json_with_retry
from .my_listings import market_listings
from .sell_plan import MARKET_FEE

STOCK_COLUMNS = ("time", "shop", "item_id", "item_name", "price", "stock", "default_stock")


def fetch_city_shops(
    session: requests.Session, api_key: str, bucket: Optional[TokenBucket] = None
) -> tuple[list[dict], Optional[str]]:
    data, err = get_json_with_retry(session, bucket, f"{BASE_URL}/torn/cityshops", api_key, timeout=30, retries=2)
    if data is None:
        return [], err
    out = []
    for shop in data.get("cityshops") or []:
        for it in shop.get("items") or []:
            stock = it.get("stock") or {}
            out.append({
                "shop": shop.get("name"),
                "item_id": int(it["id"]),
                "item_name": it.get("name"),
                "price": float(it.get("price") or 0),
                "stock": int(stock.get("current") or 0),
                "default_stock": int(stock.get("default") or 0),
            })
    return out, None


def scan_flips(
    shop_items: list[dict], market_rows: dict[int, dict], ref_prices: Optional[dict[int, float]] = None
) -> pd.DataFrame:
    """One row per city item that would resell at a profit, best first.

    Resale price is $1 under the cheapest current listing, after tax, but never
    above Torn's market value (what it has recently sold for): a listing can
    ask anything, e.g. Parachutes listed at $480m while they sell near $448m,
    under the $450m shop price. Cash is
    no object, so the limit is how many will sell: "Worth buying" assumes you
    can sell about as many as are listed now (capped by shop stock) without
    sinking the price, and items are ranked by the profit on that many.
    """
    rows = []
    for it in shop_items:
        listings = market_listings(market_rows.get(it["item_id"]) or {})
        if not listings or not it["price"]:
            continue
        floor = min(l["price"] for l in listings)
        ref = (ref_prices or {}).get(it["item_id"]) or 0
        sell = min(floor - 1, ref) if ref > 0 else floor - 1
        profit = sell * (1.0 - MARKET_FEE) - it["price"]
        if profit <= 0:
            continue
        listed = int(sum(l.get("amount") or 0 for l in listings))
        units = min(it["stock"], listed)
        rows.append({
            "Item": it["item_name"],
            "Shop": it["shop"],
            "In stock": it["stock"],
            "Buy at": it["price"],
            "Sell at": sell,
            "Market value": ref or None,
            "Profit / unit": round(profit),
            "Margin %": round(100.0 * profit / it["price"], 1),
            "Listed for sale": listed,
            "Worth buying": units,
            "Profit": round(profit * units),
            "Cost": round(it["price"] * units),
            "item_id": it["item_id"],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["Profit", "Profit / unit"], ascending=False).reset_index(drop=True)


DAILY_CAP = 100  # Torn: 100 city-shop items a day, all shops together


def daily_buy_plan(flips: pd.DataFrame, cap: int = DAILY_CAP, backups: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(buys, backups): the day's `cap` purchases, most profit per item first,
    each up to "Worth buying"; then the next in-stock items to fall back on if
    a pick has sold out by the time you reach its shop."""
    if flips.empty:
        return flips, flips
    ranked = flips[flips["Worth buying"] > 0].sort_values("Profit / unit", ascending=False)
    left = int(cap)
    picks, spare = [], []
    for _, r in ranked.iterrows():
        if left > 0:
            qty = min(int(r["Worth buying"]), left)
            left -= qty
            picks.append({**r.to_dict(), "Buy": qty})
            if qty < int(r["Worth buying"]):
                # A partly-used item also backs up the picks above it.
                spare.append({**r.to_dict(), "Buy": int(r["Worth buying"]) - qty})
        elif len(spare) < backups:
            spare.append({**r.to_dict(), "Buy": min(int(r["Worth buying"]), int(cap))})
    out = []
    for rows in (picks, spare[:backups]):
        df = pd.DataFrame(rows)
        if not df.empty:
            df["Profit"] = (df["Profit / unit"] * df["Buy"]).round()
            df["Cost"] = (df["Buy at"] * df["Buy"]).round()
        out.append(df.reset_index(drop=True))
    return out[0], out[1]


def shop_values(shop_items: list[dict], ref_prices: dict[int, float]) -> pd.DataFrame:
    """Every city item valued at Torn's market value (what it has been selling
    for), best profit per item first. Needs no market calls, so it's cheap
    enough to recheck every minute."""
    rows = []
    for it in shop_items:
        ref = ref_prices.get(it["item_id"]) or 0
        if not ref or not it["price"]:
            continue
        profit = ref * (1.0 - MARKET_FEE) - it["price"]
        rows.append({
            "Item": it["item_name"],
            "Shop": it["shop"],
            "In stock": it["stock"],
            "Buy at": it["price"],
            "Market value": ref,
            "Profit / unit": round(profit),
            "Margin %": round(100.0 * profit / it["price"], 1),
            "item_id": it["item_id"],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Profit / unit", ascending=False).reset_index(drop=True)


def stock_changes(prev: dict[int, int], shop_items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(changed, restocked) since the last check: items whose stock moved, and
    those whose stock went up. With no previous check, everything is new and
    nothing counts as a restock."""
    changed = [it for it in shop_items if prev.get(it["item_id"]) != it["stock"]]
    restocked = [it for it in changed if it["item_id"] in prev and it["stock"] > prev[it["item_id"]]]
    return changed, restocked


def restock_times(log: pd.DataFrame, item_ids) -> pd.DataFrame:
    """Each logged rise in stock for `item_ids`: when, how many, from what."""
    if log.empty:
        return pd.DataFrame(columns=["time", "item_name", "shop", "from", "stock"])
    log = log[log["item_id"].isin(set(int(i) for i in item_ids))].sort_values("time").copy()
    log["from"] = log.groupby("item_id")["stock"].shift()
    rises = log[log["from"].notna() & (log["stock"] > log["from"])]
    return rises[["time", "item_name", "shop", "from", "stock"]].reset_index(drop=True)


def log_stock(path: Path, shop_items: list[dict], stamp: str) -> int:
    if not shop_items:
        return 0
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STOCK_COLUMNS)
        if new:
            w.writeheader()
        for it in shop_items:
            w.writerow({"time": stamp, **{k: it[k] for k in STOCK_COLUMNS[1:]}})
    return len(shop_items)
