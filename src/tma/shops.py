"""City shops: which ones you can reach, which items they really buy, and
the route for a shop run.

`vendor` in the item catalog is the shop that *stocks* an item. That's only
a guess at who buys it back: the Pharmacy takes everything it stocks (plus
Hydrochloric Acid), but Big Al's stocks ~150 weapons and its Sell page took
just 5 types. So the userscript reports each shop's real Sell list while you
do a shop run, and that knowledge (data/shop_buys.json) beats the vendor
guess on the next run.

Steps were read from the City page (shops.php?step=...); Big Al's is its
own page (bigalgunshop.php) with a checkbox per weapon copy. Nikeh Sports
("This area is not yet available"), General Store, and items with no vendor
have no reachable shop, so they're never planned for one.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

SHOP_URL = "https://www.torn.com/shops.php?step={step}"
# Shops that aren't under shops.php; the userscript identifies them by page name.
OWN_PAGE_URLS = {"bigalgunshop": "https://www.torn.com/bigalgunshop.php"}

VENDOR_STEPS = {
    "Big Al's Gun Shop": "bigalgunshop",
    "Bits 'n' Bobs": "bitsnbobs",
    "Cyber Force": "cyberforce",
    "Docks": "docks",
    "Jewelry Store": "jewelry",
    "Pawn Shop": "pawnshop",
    "Pharmacy": "pharmacy",
    "Post Office": "postoffice",
    "Print Shop": "printstore",
    "Recycling Center": "recyclingcenter",
    "Sally's Sweet Shop": "candy",
    "Super Store": "super",
    "TC Clothing": "clothes",
}


STEP_NAMES = {step: name for name, step in VENDOR_STEPS.items()}


def load_shop_knowledge(path: Path) -> dict[str, dict]:
    """{step: {"name", "buys": [item ids], "rejects": [item ids], "at"}} from shop runs."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_shop_report(path: Path, report: dict) -> None:
    """Merge one shop page's report: items it listed (buys) and planned items it didn't (rejects).

    A shop's Sell page only lists items you own, so "rejects" are only the
    items we expected it to buy while you held them. Once an item has been
    seen on a shop's list it stays a buy, even if a later visit (after
    selling it) doesn't show it.
    """
    step = str(report.get("step") or "")
    if not re.fullmatch(r"[a-z]{1,40}", step):
        raise ValueError("bad step")
    buys = {int(i) for i in report.get("buys") or []}
    rejects = {int(i) for i in report.get("rejects") or []}
    if len(buys) > 5000 or len(rejects) > 5000:
        raise ValueError("report too large")
    known = load_shop_knowledge(path)
    entry = known.get(step) or {}
    all_buys = set(entry.get("buys") or []) | buys
    all_rejects = (set(entry.get("rejects") or []) | rejects) - all_buys
    known[step] = {
        "name": str(report.get("name") or entry.get("name") or STEP_NAMES.get(step, step))[:60],
        "buys": sorted(all_buys),
        "rejects": sorted(all_rejects),
        "at": int(time.time()),
    }
    Path(path).write_text(json.dumps(known, indent=2), encoding="utf-8")


def shop_step_for(item_id: int, profile: Optional[dict], known: Optional[dict] = None) -> Optional[str]:
    """Which shop to sell an item at: one seen buying it, else its vendor unless seen refusing it."""
    known = known or {}
    vendor_step = VENDOR_STEPS.get((profile or {}).get("vendor"))
    seen = [step for step, k in known.items() if item_id in (k.get("buys") or [])]
    if seen:
        return vendor_step if vendor_step in seen else seen[0]
    if vendor_step and item_id in (known.get(vendor_step, {}).get("rejects") or []):
        return None
    return vendor_step


def shop_price_for(profile: Optional[dict], item_id: Optional[int] = None, known: Optional[dict] = None) -> Optional[float]:
    """What a reachable shop pays for one unit, or None if no shop you can use buys it."""
    if not profile:
        return None
    iid = int(item_id if item_id is not None else profile.get("id") or 0)
    if shop_step_for(iid, profile, known) is None:
        return None
    price = profile.get("sell_price")
    return float(price) if price and price > 0 else None


def build_shop_run(rows: list[dict], profiles: dict, known: Optional[dict] = None) -> dict:
    """Route + per-item allowance for the userscript's shop mode.

    `rows` are Sell plan rows already decided as Shop: {item_id, qty, uid,
    is_stat, price}. Weapon/armor copies are allowed by uid only (the shop's
    Sell page lists each copy with its armoryID), so a high-roll copy of the
    same item is never swept in. A copy without a uid (paste mode) is skipped.
    """
    items: dict[int, dict] = {}
    by_shop: dict[str, dict] = {}
    for r in rows:
        iid = int(r["item_id"])
        prof = profiles.get(str(iid)) or {}
        step = shop_step_for(iid, prof, known)
        if step is None:
            continue
        if r["is_stat"]:
            if not r.get("uid"):
                continue
            entry = items.setdefault(iid, {"qty": 0, "uids": [], "price": r["price"], "step": step, "name": r.get("name") or prof.get("name")})
            entry["uids"].append(str(r["uid"]))
        else:
            entry = items.setdefault(iid, {"qty": 0, "uids": None, "price": r["price"], "step": step, "name": r.get("name") or prof.get("name")})
        entry["qty"] += int(r["qty"])
        name = STEP_NAMES.get(step) or (known or {}).get(step, {}).get("name") or step
        shop = by_shop.setdefault(step, {"step": step, "name": name, "items": 0, "payout": 0.0})
        shop["items"] += int(r["qty"])
        shop["payout"] += float(r["price"] or 0) * int(r["qty"])
    route = sorted(by_shop.values(), key=lambda s: -s["payout"])
    for s in route:
        s["url"] = OWN_PAGE_URLS.get(s["step"]) or SHOP_URL.format(step=s["step"])
        s["payout"] = int(round(s["payout"]))
    return {"route": route, "items": {str(k): v for k, v in items.items()}}
