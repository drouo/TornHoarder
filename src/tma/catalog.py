"""Item catalog (`GET /torn/items`): the name->id dictionary and per-item
profiles (type, base stats, Torn's market/shop prices).

Shared by `tools/dictionary_v2.py` (manual regeneration) and the app, which
refreshes the catalog itself once it's older than `MAX_AGE_S` so Torn's
reference prices and shop `sell_price` values don't silently go stale.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Optional

import requests

from .http_client import BASE_URL, get_json_with_retry

MAX_AGE_S = 24 * 3600


def fetch_items(session: requests.Session, api_key: str) -> tuple[list[dict], Optional[str]]:
    data, err = get_json_with_retry(session, None, f"{BASE_URL}/torn/items", api_key, timeout=30, retries=2)
    if data is None:
        return [], err
    items = data.get("items") or (data.get("torn") or {}).get("items")
    if isinstance(items, dict):
        items = list(items.values())
    return items or [], None


def build_dictionary(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for it in items:
        item_id, name = it.get("id"), it.get("name")
        if not item_id or not name:
            continue
        key = name.strip()
        k = key.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({"key": key, "id": int(item_id)})
    out.sort(key=lambda x: x["id"])
    return out


def build_profiles(items: list[dict]) -> dict[str, dict]:
    profiles = {}
    for it in items:
        details = it.get("details") or {}
        value = it.get("value") or {}
        stats = None
        base_stats = details.get("base_stats")
        if isinstance(base_stats, dict):
            stats = {k: float(v) if v is not None else 0.0 for k, v in base_stats.items()}
        name = it.get("name")
        if not name:
            continue
        profiles[str(it["id"])] = {
            "id": int(it["id"]),
            "name": name,
            "type": it.get("type"),
            "sub_type": it.get("sub_type"),
            "category": details.get("category"),
            "circulation": it.get("circulation"),
            "market_price": value.get("market_price"),
            "buy_price": value.get("buy_price"),
            "sell_price": value.get("sell_price"),
            "vendor": (value.get("vendor") or {}).get("name"),
            "stats": stats,
            "coverage": details.get("coverage"),
        }
    return profiles


def write_catalog(items: list[dict], dict_path: Path, profiles_path: Path) -> None:
    profiles_path.write_text(json.dumps(build_profiles(items), indent=2, ensure_ascii=False), encoding="utf-8")
    with dict_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["key", "id"])
        w.writeheader()
        w.writerows(build_dictionary(items))


def catalog_age_s(profiles_path: Path) -> Optional[float]:
    try:
        return time.time() - profiles_path.stat().st_mtime
    except OSError:
        return None


def refresh_catalog_if_stale(
    session: requests.Session,
    api_key: str,
    dict_path: Path,
    profiles_path: Path,
    max_age_s: float = MAX_AGE_S,
) -> tuple[bool, Optional[str]]:
    """Re-download the catalog when it's missing or older than `max_age_s`.

    Returns (refreshed, error). On error the existing files are left untouched.
    """
    age = catalog_age_s(profiles_path)
    if age is not None and age < max_age_s and dict_path.exists():
        return False, None
    items, err = fetch_items(session, api_key)
    if err is not None or not items:
        return False, err or "torn/items returned no items"
    write_catalog(items, dict_path, profiles_path)
    return True, None
