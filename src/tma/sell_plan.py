"""Decide *how* to sell each item, not just what it's worth.

Torn's item market is a pure ask book: buyers take the cheapest listing
first, so where you sit relative to the floor decides whether (and how fast)
you sell. Each item gets one plan:

- dump         cheap stuff you just want gone: list a few % *under* the floor.
               Bargain-hunters/flippers scan for below-floor listings, so this
               clears in minutes; the % given up is pocket change per unit.
- competitive  worth real money (or a big stack): floor - $1. First in line,
               without throwing away a percentage on something expensive.
- top          high-roll weapon/armor copies, or valuable items with only a
               handful of listings (slow, illiquid markets): list at the upper
               quartile and wait. Racing to the bottom there just loses money.
- shop         a city shop pays at least what the market nets after Torn's
               sales tax — sell it there instead (instant, no listing).
- keep         never list.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

# Torn item market sales tax: 5% of the sale price since 2025-06-22
# (https://wiki.torn.com/wiki/Item_Market). Anonymous listings cost more.
MARKET_FEE = 0.05

# Never auto-list under this fraction of Torn's own reference price.
REF_GUARD = 0.5

PLANS = ("dump", "competitive", "top", "shop", "keep")
PLAN_LABELS = {
    "dump": "Dump",
    "competitive": "Competitive",
    "top": "Top dollar",
    "shop": "Shop",
    "keep": "Keep",
}
LABEL_TO_PLAN = {v: k for k, v in PLAN_LABELS.items()}

# Which of an item's fast/fair/greedy prices each market plan lists from.
PLAN_BASIS = {"dump": "fast", "competitive": "fast", "top": "greedy"}


# user/inventory `cat` values that are sold by default: unequipped weapons and
# armor plus general loot and museum items. Everything else (Drug, Medical,
# Booster, Energy Drink, Supply Pack, Book, ...) is kept unless chosen.
DEFAULT_SELL_CATEGORIES = (
    "Primary",
    "Secondary",
    "Melee",
    "Defensive",
    "Clothing",
    "Collectible",
    "Jewelry",
    "Other",
    "Tool",
    "Material",
    "Car",
    "Plushie",
    "Flower",
    "Artifact",
)


@dataclass(frozen=True)
class SellRules:
    dump_undercut_pct: float = 5.0
    competitive_min_unit: float = 20_000.0
    competitive_min_stack: float = 1_000_000.0
    top_min_quality: float = 50.0
    thin_market_listings: int = 20
    sell_categories: tuple[str, ...] = DEFAULT_SELL_CATEGORIES
    # How many to keep before anything is sold (see reserve_for).
    keep_one_categories: tuple[str, ...] = ("Clothing", "Collectible")
    oc_tool_keep: int = 1
    oc_consumable_keep: int = 3
    crime_hint_keep: int = 1

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sell_categories"] = list(self.sell_categories)
        d["keep_one_categories"] = list(self.keep_one_categories)
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SellRules":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        for key in ("sell_categories", "keep_one_categories"):
            if key in known:
                known[key] = tuple(known[key])
        return cls(**known)


def reserve_for(
    item_id: int,
    profile: Optional[dict],
    category: Optional[str],
    rules: SellRules,
    oc_items: Optional[dict] = None,
    override: Optional[int] = None,
) -> tuple[int, str]:
    """How many of an item to keep, and why. Your per-item number wins; otherwise the largest of:
    Organized Crime items (reusable tools vs consumed items), items whose description says a
    crime uses them, and keep-one categories (e.g. one of every Clothing item)."""
    if override is not None:
        return max(int(override), 0), "your choice"
    keep, why = 0, ""
    oc = (oc_items or {}).get(str(item_id))
    if oc:
        n = rules.oc_consumable_keep if oc.get("consumed") else rules.oc_tool_keep
        crimes = oc.get("crimes") or []
        if n > keep:
            keep, why = n, "needed for OC: " + ", ".join(crimes[:2]) + ("…" if len(crimes) > 2 else "")
    if (profile or {}).get("crime_hint") and rules.crime_hint_keep > keep:
        keep, why = rules.crime_hint_keep, "used in crimes"
    if category in rules.keep_one_categories and keep < 1:
        keep, why = 1, f"keep 1 of each {category}"
    return keep, why


@dataclass(frozen=True)
class Decision:
    plan: str
    reason: str
    price: Optional[int]  # per-unit list price (shop: what the shop pays)
    proceeds: float  # expected cash for the whole quantity, after fees
    auto: bool  # False when the plan came from a user override


def auto_plan(
    unit_value: float,
    qty: int,
    num_listings: Optional[int],
    quality: Optional[float],
    rules: SellRules,
) -> tuple[str, str]:
    """Pick a market plan from value, stack size, liquidity, and copy quality."""
    if quality is not None and quality >= rules.top_min_quality:
        return "top", f"high-roll copy (quality {quality:.0f})"
    if unit_value >= rules.competitive_min_unit:
        if num_listings is not None and num_listings < rules.thin_market_listings:
            return "top", f"worth ${unit_value:,.0f} with only {num_listings} listings"
        return "competitive", f"worth ${unit_value:,.0f} each"
    stack = unit_value * qty
    if stack >= rules.competitive_min_stack:
        return "competitive", f"stack worth ${stack:,.0f}"
    return "dump", f"cheap (${unit_value:,.0f} each)"


def plan_price(plan: str, fast: Optional[float], fair: Optional[float], greedy: Optional[float], rules: SellRules) -> Optional[int]:
    if plan == "dump":
        return max(int(round(fast * (1.0 - rules.dump_undercut_pct / 100.0))), 1) if fast else None
    if plan == "competitive":
        return int(round(fast)) if fast else None
    if plan == "top":
        p = greedy or fair
        return int(round(p)) if p else None
    return None


def price_guards(shop_price: Optional[float], ref_price: Optional[float]) -> tuple[Optional[int], Optional[int]]:
    """(shop_min, ref_min): raise a market price to shop_min; hold anything under ref_min.

    Also embedded in the autofill script, which prices weapon/armor rows
    itself and must not undercut these either.
    """
    shop_min = math.ceil(shop_price / (1.0 - MARKET_FEE)) if shop_price and shop_price > 0 else None
    ref_min = math.ceil(REF_GUARD * ref_price) if ref_price and ref_price > 0 else None
    return shop_min, ref_min


def decide(
    *,
    qty: int,
    fast: Optional[float],
    fair: Optional[float],
    greedy: Optional[float],
    shop_price: Optional[float],
    num_listings: Optional[int],
    quality: Optional[float],
    rules: SellRules,
    override: Optional[str] = None,
    ref_price: Optional[float] = None,
    sellable: bool = True,
) -> Decision:
    """`ref_price` is Torn's own market_price for the item: an automatic plan
    that would list under REF_GUARD of it is held as Keep for a manual look
    (bad listings, crashed market, or stale data) instead of being listed."""
    qty = max(int(qty or 0), 0)
    shop = float(shop_price) if shop_price and shop_price > 0 else None

    if override in PLANS:
        plan, reason, auto = override, "your choice", False
    elif not sellable:
        return Decision("keep", "not in your sell categories", None, 0.0, True)
    else:
        plan, reason = auto_plan(float(fair or fast or 0.0), qty, num_listings, quality, rules)
        auto = True

    if plan == "keep":
        return Decision("keep", reason, None, 0.0, auto)

    price = plan_price(plan, fast, fair, greedy, rules) if plan != "shop" else None
    if auto and plan in ("dump", "competitive") and shop is not None:
        if price is None or shop >= price * (1.0 - MARKET_FEE):
            plan, reason = "shop", f"shop pays ${shop:,.0f} vs ${price or 0:,.0f} on market less {MARKET_FEE:.0%} tax"

    if plan == "shop":
        if shop is None:
            return Decision("shop", reason + " (no shop buys this)", None, 0.0, auto)
        return Decision("shop", reason, int(round(shop)), shop * qty, auto)

    shop_min, ref_min = price_guards(shop, ref_price)
    if price is not None and shop_min:
        # Listing below what a shop pays (net of tax) is strictly worse than the shop.
        price = max(price, shop_min)
    if price is not None and ref_min and price < ref_min:
        warn = f"${price:,} is under {REF_GUARD:.0%} of Torn's ${ref_price:,.0f} reference"
        if auto:
            return Decision("keep", f"⚠ check the market: {warn}", None, 0.0, auto)
        reason = f"{reason} — ⚠ {warn}"

    proceeds = price * qty * (1.0 - MARKET_FEE) if price else 0.0
    return Decision(plan, reason, price, proceeds, auto)
