"""Shared HTTP plumbing: rate limiting, retries, and Torn API error handling.

Used by both the inventory pipeline (`inventory.py`) and the item-market
pipeline (`market_fetch.py`) so there is exactly one implementation of
"take a token, call the API, retry on 429/5xx, respect Torn's cooldown".
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

import requests

from . import __version__

BASE_URL = "https://api.torn.com/v2"


class TokenBucket:
    """Simple thread-safe token bucket for capping requests/minute."""

    def __init__(self, rate_per_min: int, capacity: int | None = None) -> None:
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = capacity or rate_per_min
        self.tokens = float(self.capacity)
        self.last = time.perf_counter()
        self.lock = threading.Lock()

    def take(self, tokens: int = 1) -> None:
        while True:
            with self.lock:
                now = time.perf_counter()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate_per_sec)
                self.last = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
            time.sleep(0.01)


def session_for_requests(pool_size: int = 50) -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.headers.update({"accept": "application/json", "User-Agent": f"TornHoarder/{__version__}"})
    return session


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"ApiKey {api_key}"}


def envelope(data: dict) -> dict:
    """Torn v2 wraps payloads in {"data": {...}} for some endpoints; unwrap if present."""
    d = data.get("data")
    return d if isinstance(d, dict) else data


def api_error(data: dict) -> Optional[str]:
    err = data.get("error") or data.get("apiError")
    if isinstance(err, dict):
        return f"{err.get('code')} {err.get('error')}".strip()
    if err:
        return str(err)
    return None


def cooldown_seconds(msg: Optional[str], default: float = 15.0) -> float:
    if not msg:
        return default
    m = re.search(r"resets?\s+at\s+(\d{1,2}):(\d{2}):(\d{2})", str(msg), re.IGNORECASE)
    if not m:
        return default
    hh, mm, ss = (int(g) for g in m.groups())
    return float(min(hh * 3600 + mm * 60 + ss + 1, 60))


def get_json_with_retry(
    session: requests.Session,
    bucket: Optional[TokenBucket],
    url: str,
    api_key: str,
    params: Optional[dict] = None,
    timeout: float = 20.0,
    retries: int = 3,
    backoff0: float = 0.8,
    bucket_take: Optional[Callable[[int], None]] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """GET a Torn v2 endpoint with rate limiting, 429 cooldown, and transient retry.

    `bucket` (or `bucket_take`) is optional — pass neither to skip rate limiting
    (e.g. for a single one-off call).

    Returns (data, None) on success, or (None, error_message) once retries are exhausted.
    """
    if bucket_take is not None:
        take = bucket_take
    elif bucket is not None:
        take = bucket.take
    else:
        take = lambda n=1: None  # noqa: E731
    backoff = backoff0
    last_err: Optional[str] = None

    for attempt in range(retries):
        take(1)
        try:
            resp = session.get(url, headers=auth_headers(api_key), params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_err = str(exc)
            time.sleep(backoff)
            backoff *= 1.6
            continue

        try:
            data = resp.json()
        except ValueError:
            last_err = "non-JSON response"
            if attempt < retries - 1:
                time.sleep(1.5)
            continue

        err = api_error(data)
        if err is None:
            return data, None

        low = err.lower()
        if resp.status_code == 429 or "too many" in low:
            last_err = err
            if attempt < retries - 1:
                time.sleep(cooldown_seconds(err))
            continue

        if resp.status_code in (500, 502, 503, 504) or "0 " in low[:2]:
            last_err = err
            if attempt < retries - 1:
                time.sleep(backoff)
                backoff *= 1.6
            continue

        # Non-transient API error (bad key, invalid id, etc.) — no point retrying.
        return None, err

    return None, last_err or "exhausted retries"
