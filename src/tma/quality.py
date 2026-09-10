from __future__ import annotations

from typing import Dict, Optional


WEAPON_QUALITY_FACTOR = 10.0
ARMOR_QUALITY_FACTOR = 20.0

PREMIUM_THRESHOLD = 50.0
PREMIUM_BASE = 1.0
PREMIUM_MAX_MULT = 3.0


def weapon_quality(
    damage: float,
    base_damage: float,
    accuracy: float,
    base_accuracy: float,
) -> float:
    return ((damage - base_damage) + (accuracy - base_accuracy)) * WEAPON_QUALITY_FACTOR


def armor_quality(armor: float, base_armor: float) -> float:
    return (armor - base_armor) * ARMOR_QUALITY_FACTOR


def item_quality(
    ptype: str,
    stats: Dict[str, float],
    base: Dict[str, float],
) -> Optional[float]:
    if ptype == "Weapon":
        return weapon_quality(
            float(stats.get("damage") or 0.0),
            float(base.get("damage") or 0.0),
            float(stats.get("accuracy") or 0.0),
            float(base.get("accuracy") or 0.0),
        )
    if ptype == "Armor":
        return armor_quality(
            float(stats.get("armor") or 0.0),
            float(base.get("armor") or 0.0),
        )
    return None


def premium_multiplier(quality: float) -> float:
    if quality < PREMIUM_THRESHOLD:
        return PREMIUM_BASE
    factor = max(quality, PREMIUM_THRESHOLD) / 100.0
    return min(PREMIUM_BASE + factor * 2.0, PREMIUM_MAX_MULT)


def premium_price(base_price: float, quality: float) -> float:
    return round(base_price * premium_multiplier(quality))


def base_stats_map(profiles: Dict[str, dict]) -> Dict[int, Dict[str, float]]:
    out: Dict[int, Dict[str, float]] = {}
    for pid, prof in profiles.items():
        stats = prof.get("stats")
        if not stats:
            continue
        ptype = prof.get("type")
        if ptype == "Weapon":
            out[int(pid)] = {
                "type": "Weapon",
                "damage": float(stats.get("damage") or 0.0),
                "accuracy": float(stats.get("accuracy") or 0.0),
            }
        elif ptype == "Armor":
            out[int(pid)] = {
                "type": "Armor",
                "armor": float(stats.get("armor") or 0.0),
            }
    return out