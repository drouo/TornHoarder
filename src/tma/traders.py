"""Traders: players who buy items outright at a posted price (their
weav3r.dev price list). A trade has no market tax, so whenever a trader pays
more than a quick sale nets, the item goes to the trader instead.

Prices come from weav3r.dev's public JSON (`/api/pricelist/<player id>`):
one row per item, buyPrice 0 when they don't buy it. Whether a trader can
take a trade now comes from their Torn profile (user/<id>/profile): nobody
accepts while flying, abroad, in hospital or in jail."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

from .http_client import BASE_URL, get_json_with_retry

PRICELIST_URL = "https://weav3r.dev/api/pricelist/{id}"
PAGE_URL = "https://weav3r.dev/pricelist/{id}"
TRADE_URL = "https://www.torn.com/trade.php#step=start&userID={id}"
DEFAULT_TRADERS = ({"id": 3878831, "name": "Shekels"},)
MAX_AGE_S = 15 * 60  # traders re-price often: refetch after this
AWAY = ("Traveling", "Abroad", "Hospital", "Jail", "Federal")  # Torn status states that can't trade


def parse_pricelist(rows) -> dict[int, float]:
    """{item_id: what they pay}, items they buy only (real items, price > 0)."""
    out: dict[int, float] = {}
    for r in rows if isinstance(rows, list) else []:
        try:
            iid, price = int(r["itemId"]), float(r.get("buyPrice") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if iid > 0 and price > 0:
            out[iid] = price
    return out


def fetch_pricelist(session: requests.Session, trader_id: int) -> tuple[dict[int, float], Optional[str]]:
    try:
        resp = session.get(PRICELIST_URL.format(id=int(trader_id)), timeout=20)
        resp.raise_for_status()
        return parse_pricelist(resp.json()), None
    except (requests.RequestException, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def availability(profile: Optional[dict]) -> dict:
    """{"open", "status", "last"}: open is False while their Torn status is one
    of AWAY; None when the profile couldn't be read."""
    if not profile:
        return {"open": None, "status": "status unknown", "last": ""}
    status = profile.get("status") or {}
    last = profile.get("last_action") or {}
    state = status.get("state") or ""
    return {
        "open": state not in AWAY,
        "status": status.get("description") or state or "Okay",
        "last": last.get("status") if last.get("status") == "Online" else (last.get("relative") or last.get("status") or ""),
    }


def fetch_status(session: requests.Session, api_key: str, trader_id: int) -> tuple[dict, Optional[str]]:
    data, err = get_json_with_retry(session, None, f"{BASE_URL}/user/{int(trader_id)}/profile", api_key,
                                    timeout=15, retries=1)
    return availability((data or {}).get("profile")), err


def load_prices(path: Path) -> dict:
    """{"<trader id>": {"checked": ts, "prices": {"<item id>": price}}}."""
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def refresh_prices(
    path: Path, traders, session: requests.Session, now: Optional[float] = None, force: bool = False,
) -> tuple[dict, list[str]]:
    """Refetches each trader's list once it's older than MAX_AGE_S (or `force`),
    keeping the last good copy when a fetch fails. Returns (cache, errors)."""
    now = time.time() if now is None else now
    cache, errors = load_prices(path), []
    for t in traders:
        key = str(t["id"])
        if not force and now - (cache.get(key) or {}).get("checked", 0) < MAX_AGE_S:
            continue
        prices, err = fetch_pricelist(session, t["id"])
        if err or not prices:
            errors.append(f"{t.get('name') or key}: {err or 'empty price list'}")
            continue
        cache[key] = {"checked": int(now), "prices": {str(k): v for k, v in prices.items()}}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cache), encoding="utf-8")
    return cache, errors


def best_offers(traders, cache: dict) -> dict[int, dict]:
    """{item_id: {"price", "trader_id", "trader"}}: the best-paying trader per item."""
    best: dict[int, dict] = {}
    for t in traders:
        for k, price in ((cache.get(str(t["id"])) or {}).get("prices") or {}).items():
            iid = int(k)
            if iid not in best or price > best[iid]["price"]:
                best[iid] = {"price": float(price), "trader_id": int(t["id"]), "trader": t.get("name") or str(t["id"])}
    return best


def write_trade_plan(path: Path, runs: list[dict], pulls: list[dict], names: dict[int, str],
                     status: Optional[dict[int, dict]] = None) -> dict:
    """What the userscript shows on Torn's trade and listings pages (/trade-plan):
    per trader the items to give and whether they can trade now, and the market
    listings to pull first."""
    status = status or {}
    plan = {
        "checked": int(time.time()),
        "traders": [{
            "id": t["trader_id"], "name": names.get(t["trader_id"], str(t["trader_id"])), "total": t["total"],
            "status": status.get(t["trader_id"]),
            "items": [{"id": i["item_id"], "name": i["Item"], "qty": i["Give"], "price": i["Each"]} for i in t["items"]],
        } for t in runs],
        "pull": [{"id": r.get("item_id"), "name": r.get("Item"), "amount": r.get("Amount"), "price": r.get("Your price"),
                  "trader": r.get("Pull for"), "pays": r.get("Trader pays")} for r in pulls],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(plan), encoding="utf-8")
    return plan
