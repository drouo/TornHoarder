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


def scan_flips(shop_items: list[dict], market_rows: dict[int, dict]) -> pd.DataFrame:
    """One row per city item that would resell at a profit, best total first.

    Resale price is $1 under the cheapest current listing, after tax.
    """
    rows = []
    for it in shop_items:
        listings = market_listings(market_rows.get(it["item_id"]) or {})
        if not listings or not it["price"]:
            continue
        floor = min(l["price"] for l in listings)
        resale = (floor - 1) * (1.0 - MARKET_FEE)
        profit = resale - it["price"]
        if profit <= 0:
            continue
        rows.append({
            "Item": it["item_name"],
            "Shop": it["shop"],
            "In stock": it["stock"],
            "Shop price": it["price"],
            "Cheapest listing": floor,
            "Profit / unit": round(profit),
            "Margin %": round(profit / it["price"] * 100.0, 1),
            "Profit if all stock": round(profit * it["stock"]),
            "Cash needed": round(it["price"] * it["stock"]),
            "Listed for sale": int(sum(l.get("amount") or 0 for l in listings)),
            "item_id": it["item_id"],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["Profit if all stock", "Profit / unit"], ascending=False).reset_index(drop=True)


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
