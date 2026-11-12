"""Your own Torn profile, loadout, bars and money (user/profile, user/equipment,
user/bars, user/money), plus what each equipped item is worth today."""

from __future__ import annotations

from typing import Optional

import requests

from .http_client import BASE_URL, get_json_with_retry
from .market_enrichment import price_at_quality, quality_from_stats

SECTIONS = ("profile", "equipment", "bars", "money")
SLOTS = {1: "Primary", 2: "Secondary", 3: "Melee", 5: "Temporary",
         6: "Head", 4: "Body", 9: "Hands", 7: "Legs", 8: "Feet"}


def fetch_profile_bundle(session: requests.Session, api_key: str) -> dict:
    """{profile, equipment, bars, money, errors}; a failed section is None and its error is listed."""
    out: dict = {"errors": []}
    for name in SECTIONS:
        data, err = get_json_with_retry(session, None, f"{BASE_URL}/user/{name}", api_key, timeout=20, retries=1)
        out[name] = (data or {}).get(name) if data is not None else None
        if err:
            out["errors"].append(f"user/{name}: {err}")
    return out


def away_from_torn(bundle: Optional[dict]) -> Optional[str]:
    """Torn's status line ("Traveling to Mexico", "In Mexico") when you're in the
    air or abroad, where city shops and the Item Market are closed; else None."""
    status = ((bundle or {}).get("profile") or {}).get("status") or {}
    if status.get("state") in ("Traveling", "Abroad"):
        return status.get("description") or status["state"]
    return None


def item_image_url(item_id: int) -> str:
    return f"https://www.torn.com/images/items/{int(item_id)}/large.png"


def equipped_value(item: dict, summary: Optional[dict], ref_price: Optional[float] = None) -> Optional[float]:
    """Today's value of one equipped item: weapon/armor copies at their quality
    on the market's quality curve (fair), else the item's fair price, else
    Torn's reference price."""
    summary = summary or {}
    stats = item.get("stats") or {}
    payload = summary.get("copy_tool") or {}
    if stats and payload.get("points"):
        q = stats.get("quality")
        if q is None:
            q = quality_from_stats(payload, stats)
        if q is not None:
            fair = price_at_quality(payload, float(q)).get("fair")
            if fair:
                return float(fair)
    for key in ("fair_price", "fast_sell_price"):
        v = summary.get(key)
        if v is not None and v == v:
            return float(v)
    return float(ref_price) if ref_price else None


def total_money(money: Optional[dict], point_price: Optional[float] = None) -> dict:
    """Cash by place, points (and their value), and Torn's daily networth."""
    m = money or {}

    def amount(v):
        if isinstance(v, dict):
            return float(v.get("amount", v.get("money")) or 0)
        return float(v or 0)

    cash = {k: amount(m.get(k)) for k in ("wallet", "vault", "city_bank", "cayman_bank", "company", "faction") if k in m}
    points = int(m.get("points") or 0)
    networth = m.get("daily_networth")
    return {
        "cash": cash,
        "cash_total": sum(cash.values()),
        "points": points,
        "points_value": points * point_price if point_price else None,
        "networth": amount(networth) if networth is not None else None,
    }
