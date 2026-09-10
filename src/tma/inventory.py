from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd
import requests

from .http_client import BASE_URL, envelope, get_json_with_retry

CAT_TIMEOUT_S = 120.0

INVENTORY_CATEGORIES = (
    "Collectible",
    "Clothing",
    "Other",
    "Tool",
    "Melee",
    "Defensive",
    "Material",
    "Car",
    "Primary",
    "Secondary",
    "Book",
    "Special",
    "Supply Pack",
    "Temporary",
    "Enhancer",
    "Artifact",
    "Flower",
    "Booster",
    "Medical",
    "Candy",
    "Jewelry",
    "Alcohol",
    "Plushie",
    "Drug",
    "Energy Drink",
)

CATEGORY_LABELS = {
    "Defensive": "Defensive (armor)",
    "Energy Drink": "Energy Drink",
}

MAX_PAGES = 200


@dataclass(frozen=True)
class InventoryEntry:
    item_id: int
    name: str
    amount: int
    equipped: bool
    uid: Optional[str]


@dataclass(frozen=True)
class InventoryResult:
    entries: list[InventoryEntry]
    errors: list[str]


def fetch_item_stats(
    session: requests.Session,
    api_key: str,
    uid: int | str,
    bucket_take: Optional[Callable[[int], None]] = None,
) -> Optional[dict]:
    data, err = get_json_with_retry(
        session,
        bucket=None,
        url=f"{BASE_URL}/torn/{uid}",
        api_key=api_key,
        params={"selections": "itemdetails"},
        timeout=20,
        retries=1,
        bucket_take=bucket_take,
    )
    if err is not None or data is None:
        return None
    details = (data.get("itemdetails") or {}).get("stats") or {}
    stats = {
        "damage": details.get("damage"),
        "accuracy": details.get("accuracy"),
        "armor": details.get("armor"),
        "quality": details.get("quality"),
    }
    if "uid" in (data.get("itemdetails") or {}):
        stats["uid"] = data["itemdetails"]["uid"]
    return stats


def fetch_item_stats_many(
    session: requests.Session,
    api_key: str,
    uids: list[int | str],
    bucket_take: Optional[Callable[[int], None]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[dict, list[str]]:
    stats_by_uid: dict = {}
    errors: list[str] = []
    for i, uid in enumerate(uids, start=1):
        if on_progress:
            on_progress(i, len(uids))
        last: Optional[Exception] = None
        for _ in range(3):
            try:
                stats = fetch_item_stats(session, api_key, uid, bucket_take=bucket_take)
                last = None
                if stats is not None:
                    stats_by_uid[str(uid)] = stats
                else:
                    stats_by_uid[str(uid)] = {}
                break
            except requests.RequestException as exc:
                last = exc
                time.sleep(0.6)
        if last is not None:
            errors.append(f"uid {uid}: {last}")
    return stats_by_uid, errors[:5]


def _parse_page(data: dict, category: Optional[str]) -> list[InventoryEntry]:
    d = envelope(data)
    inventory = d.get("inventory") or []
    if isinstance(inventory, dict):
        inventory = inventory.get("items") or []
    entries: list[InventoryEntry] = []

    for it in inventory:
        if not isinstance(it, dict):
            continue
        item_id = it.get("id")
        name = it.get("name")
        if item_id is None or not name:
            continue
        if it.get("faction_owned"):
            continue
        amount = int(it.get("amount") or 0)
        if amount <= 0:
            continue
        uid = it.get("uid")
        entries.append(
            InventoryEntry(
                item_id=int(item_id),
                name=str(name),
                amount=amount,
                equipped=bool(it.get("equipped")),
                uid=str(uid) if uid is not None else None,
            )
        )
    return entries


def _has_more(data: dict, offset: int, page_len: int) -> bool:
    meta = {}
    d = envelope(data)
    meta.update(d.get("_metadata") or {})
    meta.update(data.get("_metadata") or {})
    if "total" in meta:
        return int(meta["total"] or 0) > offset + page_len
    links = meta.get("links") or {}
    if links.get("next"):
        return True
    if "more" in meta:
        return bool(meta["more"])
    return page_len >= 100


def _fetch_category(
    session: requests.Session,
    api_key: str,
    category: Optional[str],
    on_progress: Optional[Callable[[str, str, Optional[int]], None]] = None,
    bucket_take: Optional[Callable[[int], None]] = None,
) -> tuple[list[InventoryEntry], list[str]]:
    entries: list[InventoryEntry] = []
    errors: list[str] = []
    offset = 0
    label = category or "all"
    deadline = time.monotonic() + CAT_TIMEOUT_S

    for _ in range(MAX_PAGES):
        if on_progress:
            on_progress(label, "page", offset)
        params = {"limit": 100, "offset": offset}
        if category:
            params["cat"] = category

        data, err = get_json_with_retry(
            session,
            bucket=None,
            url=f"{BASE_URL}/user/inventory",
            api_key=api_key,
            params=params,
            timeout=20,
            retries=3,
            bucket_take=bucket_take,
        )
        if data is None:
            errors.append(f"{category or 'all'} / offset {offset}: {err}")
            return entries, errors

        page = _parse_page(data, category)
        entries.extend(page)

        if time.monotonic() >= deadline:
            errors.append(f"{label}: timed out after {CAT_TIMEOUT_S:.0f}s")
            break
        if not page:
            break
        if not _has_more(data, offset, len(page)):
            break
        offset += len(page)

    if on_progress:
        on_progress(label, "done", len(entries))

    return entries, errors


def to_rows(entries: list[InventoryEntry], include_equipped: bool = False):
    kept = [e for e in entries if include_equipped or not e.equipped]
    records = [{"name": e.name, "id": e.item_id, "quantity": e.amount, "uid": e.uid or ""} for e in kept]
    if records:
        frame = pd.DataFrame(records).sort_values("name").reset_index(drop=True)
    else:
        frame = pd.DataFrame(records, columns=["name", "id", "quantity", "uid"])
    agg = [(e.item_id, e.amount) for e in kept]
    return kept, frame, agg


def _dedupe(entries: list[InventoryEntry]) -> list[InventoryEntry]:
    seen = set()
    deduped: list[InventoryEntry] = []
    for e in entries:
        key = (e.item_id, e.uid)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped


def fetch_inventory(
    session: requests.Session,
    api_key: str,
    categories: Optional[tuple[str, ...]] = None,
    on_progress: Optional[Callable[[str, str, Optional[int]], None]] = None,
    bucket_take: Optional[Callable[[int], None]] = None,
) -> InventoryResult:
    entries: list[InventoryEntry] = []
    errors: list[str] = []

    if not categories:
        categories = INVENTORY_CATEGORIES

    for category in categories:
        try:
            cat_entries, cat_errors = _fetch_category(
                session, api_key, category, on_progress, bucket_take=bucket_take
            )
        except requests.RequestException as exc:
            cat_entries, cat_errors = [], [f"{category or 'all'}: request failed: {exc}"]
        entries.extend(cat_entries)
        errors.extend(cat_errors)

    return InventoryResult(entries=_dedupe(entries), errors=errors[:5])