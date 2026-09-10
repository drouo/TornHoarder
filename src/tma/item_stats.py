from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

MIN_FIT_SAMPLES = 20
FLAG_RATIO = 1.30

STAT_TYPES = ("Weapon", "Armor")
WEAPON_CATEGORIES = ("Primary", "Secondary", "Melee", "Temporary")
ARMOR_MODEL_KEY = "Armor"

TIERS = (
    (0.99, "Elite"),
    (0.95, "Rare"),
    (0.80, "Premium"),
    (0.50, "Solid"),
    (0.00, "Common"),
)


@dataclass(frozen=True)
class StatInfo:
    model_key: Optional[str]
    implied_value: Optional[float]
    tier: Optional[str]
    features: Dict[str, float]
    market_price: Optional[float]
    has_stats: bool


def load_profiles(path: str | Path) -> Dict[str, dict]:
    with Path(path).open("r", encoding="utf-8") as f:
        raw: Dict[str, dict] = json.load(f)
    profiles: Dict[str, dict] = {}
    for pid, prof in raw.items():
        prof["id"] = int(prof.get("id", pid))
        profiles[str(prof["id"])] = prof
    return profiles


def item_model_key(profile: dict) -> Optional[str]:
    if profile.get("type") == "Weapon":
        cat = profile.get("category")
        return cat if cat in WEAPON_CATEGORIES else None
    if profile.get("type") == "Armor":
        return ARMOR_MODEL_KEY
    return None


def item_features(profile: dict) -> Optional[Dict[str, float]]:
    ptype = profile.get("type")
    if ptype == "Weapon":
        stats = profile.get("stats") or {}
        if not stats.get("damage") and not stats.get("accuracy"):
            return None
        return {
            "damage": float(stats.get("damage") or 0.0),
            "accuracy": float(stats.get("accuracy") or 0.0),
            "armor": float(stats.get("armor") or 0.0),
        }
    if ptype == "Armor":
        cov = profile.get("coverage") or []
        if not cov:
            return None
        by_name = {c.get("name"): float(c.get("value") or 0.0) for c in cov}
        full = by_name.pop("Full Body", 0.0)
        others = list(by_name.values())
        return {
            "full_body": full,
            "coverage_avg": float(np.mean(others)) if others else 0.0,
            "coverage_min": float(np.min(others)) if others else 0.0,
            "coverage_parts": float(len(others)),
        }
    return None


def _tier_label(pct: float) -> str:
    for bound, label in TIERS:
        if pct >= bound:
            return label
    return TIERS[-1][1]


class StatValueModel:
    def __init__(self) -> None:
        self.models: Dict[str, dict] = {}
        self.value_by_id: Dict[str, Dict[str, tuple]] = {}

    def fit(self, profiles: Dict[str, dict]) -> None:
        groups: Dict[str, list] = {}
        for pid, prof in profiles.items():
            key = item_model_key(prof)
            if key is None:
                continue
            feats = item_features(prof)
            price = prof.get("market_price")
            if feats is None or price is None or price <= 0.0:
                continue
            groups.setdefault(key, []).append((pid, prof, feats, float(price)))

        for key, rows in groups.items():
            if len(rows) < MIN_FIT_SAMPLES:
                continue
            self._fit_key(key, rows)

        self._finalize(profiles)

    def _fit_key(self, key: str, rows: list) -> None:
        feat_cols = list(next(r[2] for r in rows).keys()) + ["log_circulation"]
        X0 = np.array(
            [[1.0] + [r[2][c] for c in feat_cols[:-1]] + [math.log(max(float(r[1].get("circulation") or 1.0), 1.0))] for r in rows],
            dtype=float,
        )
        y0 = np.log(np.array([r[3] for r in rows], dtype=float))
        weights = np.sqrt([max(float(r[1].get("circulation") or 0.0), 1.0) for r in rows])

        coef, resid = _wls(X0, y0, weights)
        sigma = float(np.std(resid))
        keep = np.abs(resid) <= 3.0 * sigma if sigma > 0 else np.ones(len(rows), dtype=bool)
        if keep.sum() >= MIN_FIT_SAMPLES:
            X = X0[keep]
            y = y0[keep]
            w = weights[keep]
            coef, resid = _wls(X, y, w)
        else:
            X, y, w = X0, y0, weights

        y_hat = X @ coef
        ss_res = float(np.sum(w * (y - y_hat) ** 2))
        ss_tot = float(np.sum(w * (y - np.average(y, weights=w)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        self.models[key] = {
            "coef": coef,
            "features": feat_cols,
            "n": int(keep.sum()),
            "n_total": int(len(rows)),
            "r2": r2,
            "log_min": float(np.min(y)),
            "log_max": float(np.max(y)),
        }

    def _finalize(self, profiles: Dict[str, dict]) -> None:
        for key, data in self.models.items():
            feat_cols = data["features"]
            vals: Dict[str, float] = {}
            for pid, prof in profiles.items():
                if item_model_key(prof) != key:
                    continue
                feats = item_features(prof)
                if feats is None or prof.get("market_price") is None or prof.get("market_price") <= 0.0:
                    continue
                X = np.array(
                    [1.0] + [feats[c] for c in feat_cols[:-1]] + [math.log(max(float(prof.get("circulation") or 1.0), 1.0))]
                )
                logp = float(np.clip(X @ data["coef"], data["log_min"], data["log_max"]))
                vals[pid] = math.exp(logp)

            arr = np.array(sorted(vals.values()))
            for pid, pred in vals.items():
                pct = float((np.searchsorted(arr, pred) + 0.5) / arr.size)
                tier = _tier_label(pct)
                self.value_by_id.setdefault(pid, {})[key] = (pred, pct, tier)


def _wls(X: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple:
    W = np.sqrt(w)
    coef, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None)
    resid = y - X @ coef
    return coef, resid


def sidecar(model: StatValueModel, profiles: Dict[str, dict], item_id: int) -> StatInfo:
    prof = profiles.get(str(item_id))
    if not prof:
        return StatInfo(None, None, None, {}, None, False)
    key = item_model_key(prof)
    feats = item_features(prof)
    if key is None or feats is None:
        return StatInfo(None, None, None, feats or {}, prof.get("market_price"), False)
    entry = model.value_by_id.get(str(item_id), {}).get(key)
    if entry is None:
        return StatInfo(key, None, None, feats, prof.get("market_price"), True)
    pred, pct, tier = entry
    return StatInfo(key, pred, tier, feats, prof.get("market_price"), True)


ELEVATED_TIERS = ("Premium", "Rare", "Elite")


def stat_warnings(
    model: StatValueModel,
    profiles: Dict[str, dict],
    df_summary: pd.DataFrame,
    price_col: str = "fair_price",
) -> List[dict]:
    warnings: List[dict] = []
    for _, row in df_summary.iterrows():
        try:
            item_id = int(row["item_id"])
        except (TypeError, ValueError):
            continue
        info = sidecar(model, profiles, item_id)
        fair = float(row.get(price_col) or 0.0)
        market_price = info.market_price or 0.0
        if fair <= 0 or market_price <= 0:
            continue
        if fair >= market_price / FLAG_RATIO:
            continue
        elevated = info.model_key is not None and info.tier in ELEVATED_TIERS
        warnings.append(
            {
                "item_id": item_id,
                "item_name": row["item_name"],
                "my_quantity": row.get("my_quantity"),
                "fair_price": fair,
                "market_price": market_price,
                "implied_value": info.implied_value,
                "tier": info.tier,
                "ratio": market_price / fair,
                "elevated": elevated,
            }
        )
    warnings.sort(key=lambda w: (not w["elevated"], -w["ratio"]))
    return warnings