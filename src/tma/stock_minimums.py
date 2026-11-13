"""Keep stocked: minimums for items you always want on hand (first aid kits,
Ipecac, ...) and for your equipped guns' ammo, what's short, and where to buy
the difference: the cheaper of the city shop and the Item Market.

Minimums are keyed "item:<id>", "ammo:primary" or "ammo:secondary"; ammo
follows whatever gun is equipped in that slot (Standard rounds)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .inventory import inventory_category_for_profile
from .shops import buy_url

AMMO_SLOTS = {"ammo:primary": (1, "Primary"), "ammo:secondary": (2, "Secondary")}
# Links carry when stock was checked, so the userscript only counts purchases
# made since then against the shortfall (and never buys it twice).
MARKET_URL = ("https://www.torn.com/page.php?sid=ItemMarket&thneed={id}:{need}:{cap}:{checked}"
              "#/market/view=search&itemID={id}")
AMMO_URL = "https://www.torn.com/ammo.php?thammo={need}&thammoname={name}"
MAX_OVER_VALUE = 1.10  # never pay more than 10% over Torn's market value
RESTOCK_URL = "https://www.torn.com/index.php?threstock=1"  # starts the userscript's restock run


def item_ids(minimums: dict[str, int]) -> list[int]:
    return sorted(int(k.split(":", 1)[1]) for k in minimums if k.startswith("item:"))


def categories_for(minimums: dict[str, int], profiles: dict) -> tuple[str, ...]:
    """The inventory categories to read so every item minimum can be counted."""
    cats = {inventory_category_for_profile(profiles.get(str(i))) for i in item_ids(minimums)}
    return tuple(sorted(c for c in cats if c))


def check_stock(
    minimums: dict[str, int], held: dict[int, int], equipment: Optional[list], profiles: dict,
    now: Optional[float] = None,
) -> list[dict]:
    """One row per minimum: what you have, the minimum, and how many to buy."""
    checked = int(now if now is not None else time.time())
    rows = []
    guns = {e.get("slot"): e for e in (equipment or []) if e.get("ammo")}
    for key, minimum in sorted(minimums.items()):
        if key in AMMO_SLOTS:
            slot, label = AMMO_SLOTS[key]
            gun = guns.get(slot)
            if not gun:
                continue  # nothing equipped there, or it takes no ammo
            ammo = gun["ammo"]
            have = int(ammo.get("quantity") or 0) if ammo.get("type", "Standard") == "Standard" else 0
            rows.append({
                "key": key, "kind": "ammo", "name": f"{ammo.get('name')} ({label}: {gun.get('name')})",
                "ammo_name": ammo.get("name"), "have": have, "min": int(minimum),
            })
            continue
        iid = int(key.split(":", 1)[1])
        prof = profiles.get(str(iid)) or {}
        rows.append({
            "key": key, "kind": "item", "item_id": iid, "name": prof.get("name") or f"item {iid}",
            "have": int(held.get(iid, 0)), "min": int(minimum),
        })
    for r in rows:
        r["checked"] = checked
        r["need"] = max(r["min"] - r["have"], 0)
        r.update(where_to_buy(r, profiles))
    return rows


def where_to_buy(row: dict, profiles: dict) -> dict:
    """{"source", "unit", "url"} for a row's shortfall (url None when nothing is needed)."""
    need = row.get("need", 0)
    if row["kind"] == "ammo":
        from urllib.parse import quote
        return {"source": "Big Al's ammo", "unit": None,
                "url": AMMO_URL.format(need=need, name=quote(row["ammo_name"] or "")) if need else None}
    prof = profiles.get(str(row["item_id"])) or {}
    shop_price, market = prof.get("buy_price") or 0, prof.get("market_price") or 0
    if shop_price and (not market or shop_price <= market) and prof.get("vendor"):
        return {"source": prof["vendor"], "unit": shop_price,
                "url": _stamped(buy_url(prof["vendor"], {row["item_id"]: need}), row) if need else None}
    cap = round(market * MAX_OVER_VALUE) if market else 0
    return {"source": "Item Market", "unit": market or None,
            "url": MARKET_URL.format(id=row["item_id"], need=need, cap=cap, checked=row.get("checked", 0)) if need else None}


def _stamped(url: Optional[str], row: dict) -> Optional[str]:
    return f"{url}&thchk={row.get('checked', 0)}" if url else url


def restock_route(rows: list[dict]) -> list[dict]:
    """The stops of a restock run, in order: one per city shop (all its items
    at once), then each Item Market item, then the ammo page."""
    shops: dict[str, dict] = {}
    market, ammo = [], []
    for r in rows:
        if r["need"] <= 0 or not r.get("url"):
            continue
        what = f"{r['need']:,} {r['ammo_name'] if r['kind'] == 'ammo' else r['name']}"
        if r["kind"] == "ammo":
            ammo.append({"label": f"Ammo: {what}", "url": r["url"]})
        elif r["source"] == "Item Market":
            market.append({"label": f"Item Market: {what}", "url": r["url"]})
        else:
            stop = shops.setdefault(r["source"], {"items": {}, "what": []})
            stop["items"][r["item_id"]] = r["need"]
            stop["what"].append(what)
    checked = rows[0].get("checked", 0) if rows else 0
    route = [{"label": f"{shop}: {', '.join(s['what'])}", "url": _stamped(buy_url(shop, s["items"]), {"checked": checked})}
             for shop, s in shops.items()]
    return route + market + ammo


def as_reserves(minimums: dict[str, int]) -> dict[int, int]:
    """Item minimums as sell-plan reserves: never sell below them."""
    return {int(k.split(":", 1)[1]): int(v) for k, v in minimums.items() if k.startswith("item:")}


def write_status(path: Path, rows: list[dict], now: Optional[float] = None) -> dict:
    """What the in-Torn badge shows: only what's short, plus when it was checked."""
    status = {
        "checked": int(now if now is not None else (rows[0].get("checked") if rows else time.time())),
        "short": [{k: r.get(k) for k in ("name", "have", "min", "need", "source", "url")} for r in rows if r["need"] > 0],
        "route": restock_route(rows),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status), encoding="utf-8")
    return status


def load_status(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"checked": 0, "short": []}
