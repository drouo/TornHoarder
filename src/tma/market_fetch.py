"""Fetch item-market listings (`GET /market/{item_id}/itemmarket`) for pricing.

Previously this lived inline in `app/streamlit_app.py` as `attempt_call`/
`fetch_100`, cycling through three different auth header styles per retry
(a leftover from early API trial-and-error) and fetching one item at a time.
Torn's v2 auth format is the `ApiKey` header (see `http_client.auth_headers`),
so that cycling is gone, and items are now fetched concurrently — the shared
TokenBucket still caps actual request throughput, so this only overlaps
network wait time rather than exceeding Torn's rate limit.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import requests

from .http_client import BASE_URL, TokenBucket, get_json_with_retry

MAX_LISTINGS = 100


def fetch_item_market(
    session: requests.Session,
    bucket: TokenBucket,
    api_key: str,
    item_id: int,
    my_quantity: int,
) -> dict:
    """Fetch up to `MAX_LISTINGS` sell listings for one item as a wide row dict."""
    data, err = get_json_with_retry(
        session,
        bucket,
        url=f"{BASE_URL}/market/{item_id}/itemmarket",
        api_key=api_key,
        params={"limit": MAX_LISTINGS, "offset": 0},
        timeout=15,
        retries=3,
    )
    if data is None:
        return {"item_id": item_id, "my_quantity": my_quantity, "error": f"API error: {err}"}

    itemmarket = (data or {}).get("itemmarket", {}) or {}
    item = itemmarket.get("item", {}) or {}
    listings = itemmarket.get("listings", []) or []
    n = min(len(listings), MAX_LISTINGS)

    row: dict[str, object] = {
        "item_id": item.get("id", item_id),
        "item_name": item.get("name"),
        "item_type": item.get("type"),
        "average_price": item.get("average_price"),
        "my_quantity": my_quantity,
    }
    for i, listing in enumerate(listings[:n], start=1):
        row[f"price_{i}"] = listing.get("price")
        row[f"amount_{i}"] = listing.get("amount")
        details = listing.get("item_details") or {}
        stats = details.get("stats") or {}
        row[f"quality_{i}"] = stats.get("quality")
        row[f"damage_{i}"] = stats.get("damage")
        row[f"accuracy_{i}"] = stats.get("accuracy")
        row[f"armor_{i}"] = stats.get("armor")
    for i in range(n + 1, MAX_LISTINGS + 1):
        row[f"price_{i}"] = None
        row[f"amount_{i}"] = None
        row[f"quality_{i}"] = None
        row[f"damage_{i}"] = None
        row[f"accuracy_{i}"] = None
        row[f"armor_{i}"] = None
    return row


def fetch_all_item_markets(
    session: requests.Session,
    bucket: TokenBucket,
    api_key: str,
    item_ids: list[int],
    qty_by_id: dict[int, int],
    max_workers: int = 5,
    on_progress: Optional[Callable[[int, int, dict], None]] = None,
    deadline_s: Optional[float] = None,
) -> tuple[dict[int, dict], bool]:
    """Fetch market listings for many items concurrently.

    Returns (rows_by_item_id, truncated). `truncated` is True if `deadline_s`
    was hit before every item finished — remaining items are simply omitted
    from the result rather than raising.
    """
    rows: dict[int, dict] = {}
    start = time.monotonic()
    truncated = False

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {
            pool.submit(fetch_item_market, session, bucket, api_key, iid, qty_by_id[iid]): iid
            for iid in item_ids
        }
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            iid = futures[future]
            rows[iid] = future.result()
            done += 1
            if on_progress:
                on_progress(done, total, rows[iid])
            if deadline_s is not None and (time.monotonic() - start) > deadline_s:
                truncated = True
                for f in futures:
                    f.cancel()  # only affects futures not yet started
                break

    return rows, truncated
