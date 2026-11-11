from __future__ import annotations

import math

import numpy as np
import pandas as pd


EXCLUSIVE_TOTAL_UNITS_THRESHOLD = 200.0
EXCLUSIVE_DOMINANCE_SHARE = 0.50
EXCLUSIVE_HIGH_FACTOR = 10.0
LOW_OUTLIER_FACTOR = 0.5  # listings under half the clean median aren't the "floor"

QUALITY_STEP = 5.0
MIN_QUALITY_SAMPLES = 5
MIN_FIT_SAMPLES = 12


def wide_to_long(df: pd.DataFrame) -> pd.DataFrame:
    price_cols = sorted([c for c in df.columns if c.startswith("price_")], key=lambda x: int(x.split("_")[1]))
    amount_cols = sorted([c for c in df.columns if c.startswith("amount_")], key=lambda x: int(x.split("_")[1]))
    stat_cols = {
        base: sorted([c for c in df.columns if c.startswith(f"{base}_")], key=lambda x: int(x.split("_")[1]))
        for base in ("quality", "damage", "accuracy", "armor")
    }

    records: list[dict] = []
    for _, row in df.iterrows():
        base = {
            "item_id": int(row["item_id"]),
            "item_name": row["item_name"],
            "item_type": row["item_type"],
            "average_price": row["average_price"],
            "my_quantity": row["my_quantity"],
        }

        for idx, (p_col, q_col) in enumerate(zip(price_cols, amount_cols), start=1):
            price = row[p_col]
            qty = row[q_col]

            if pd.isna(price) or pd.isna(qty) or float(qty) <= 0:
                continue

            record: dict = {
                **base,
                "listing_rank": idx,
                "price": float(price),
                "quantity": float(qty),
            }
            for base_name, col_list in stat_cols.items():
                col = col_list[idx - 1] if idx <= len(col_list) else None
                record[f"listing_{base_name}"] = float(row[col]) if (col and not pd.isna(row[col])) else None
            records.append(record)

    cols = [
        "item_id",
        "item_name",
        "item_type",
        "average_price",
        "my_quantity",
        "listing_rank",
        "price",
        "quantity",
        "listing_quality",
        "listing_damage",
        "listing_accuracy",
        "listing_armor",
    ]
    return pd.DataFrame.from_records(records, columns=cols)


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    q = float(q)
    order = np.argsort(values)
    v = values[order]
    w = weights[order].astype(float)

    if q <= 0:
        return float(v[0])
    if q >= 1:
        return float(v[-1])

    total = float(np.sum(w))
    if total <= 0:
        return float(np.quantile(v, q))

    cum = np.cumsum(w)
    idx = int(np.searchsorted(cum, q * total, side="left"))
    return float(v[min(idx, len(v) - 1)])


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    return _weighted_quantile(values, weights, 0.5)


def _weighted_price_quantile(df: pd.DataFrame, q: float) -> float:
    d = df.sort_values("price")
    return _weighted_quantile(d["price"].to_numpy(dtype=float), d["quantity"].to_numpy(dtype=float), q)


def _unweighted_price_quantile(df: pd.DataFrame, q: float) -> float:
    return float(np.quantile(df["price"].to_numpy(dtype=float), q))


def add_price_stats_for_item(df_item: pd.DataFrame) -> pd.DataFrame:
    df_item = df_item.copy()

    prices = df_item["price"].to_numpy(dtype=float)
    qty = df_item["quantity"].to_numpy(dtype=float)

    total_w = float(np.sum(qty))
    if total_w <= 0:
        median = float(np.median(prices))
        q1 = float(np.quantile(prices, 0.25))
        q3 = float(np.quantile(prices, 0.75))
        mad = float(np.median(np.abs(prices - median)))
    else:
        median = _weighted_median(prices, qty)
        q1 = _weighted_quantile(prices, qty, 0.25)
        q3 = _weighted_quantile(prices, qty, 0.75)
        mad = _weighted_median(np.abs(prices - median), qty)

    df_item["price_median"] = median
    df_item["price_q1"] = q1
    df_item["price_q3"] = q3
    df_item["price_iqr"] = q3 - q1
    df_item["price_mad"] = mad

    df_item["robust_z"] = (0.6745 * (df_item["price"] - median) / mad) if mad > 0 else 0.0
    df_item["is_extreme_price"] = df_item["robust_z"].abs() > 3.0
    return df_item


def add_depth_features_for_item(df_item: pd.DataFrame) -> pd.DataFrame:
    df_item = df_item.copy().sort_values("price").reset_index(drop=True)
    df_item["cum_qty"] = df_item["quantity"].cumsum()
    total = float(df_item["quantity"].sum())
    df_item["cum_qty_pct"] = (df_item["cum_qty"] / total) if total > 0 else 0.0
    return df_item


def mark_suspected_anchors_for_item(
    df_item: pd.DataFrame,
    z_threshold: float = 5.0,
    front_depth_pct: float = 0.02,
    back_depth_pct: float = 0.02,
    max_level_units_for_anchor: float = 50.0,
) -> pd.DataFrame:
    df_item = df_item.copy()

    level = df_item.groupby("price", as_index=False)["quantity"].sum().rename(columns={"quantity": "level_qty"})
    total_qty = float(level["level_qty"].sum())
    level["level_share"] = (level["level_qty"] / total_qty) if total_qty > 0 else 0.0

    df_item = df_item.merge(level[["price", "level_share"]], on="price", how="left")

    total_qty_all = float(df_item["quantity"].sum())
    max_level_share = float(df_item["level_share"].max())
    median_price = float(df_item["price_median"].iloc[0])

    exclusive_mode = (total_qty_all <= EXCLUSIVE_TOTAL_UNITS_THRESHOLD) or (max_level_share >= EXCLUSIVE_DOMINANCE_SHARE)

    shallow_front = df_item["cum_qty_pct"] < front_depth_pct
    shallow_back = df_item["cum_qty_pct"] > (1.0 - back_depth_pct)

    if exclusive_mode and median_price > 0:
        extreme_high = df_item["price"] > (median_price * EXCLUSIVE_HIGH_FACTOR)
        small_qty = df_item["quantity"] <= max_level_units_for_anchor
        df_item["is_suspected_anchor"] = extreme_high & (shallow_front | shallow_back | small_qty)
    else:
        extreme_mask = df_item["robust_z"].abs() > z_threshold
        small_qty = df_item["quantity"] < max_level_units_for_anchor
        df_item["is_suspected_anchor"] = extreme_mask & (shallow_front | shallow_back) & small_qty

    return df_item


def enrich_item_orders(df_item: pd.DataFrame) -> pd.DataFrame:
    df_item = add_price_stats_for_item(df_item)
    df_item = add_depth_features_for_item(df_item)
    df_item = mark_suspected_anchors_for_item(df_item)
    return df_item


def compute_price_suggestions_for_item(df_item: pd.DataFrame) -> dict:
    item_id = int(df_item["item_id"].iloc[0])
    item_name = df_item["item_name"].iloc[0]
    item_type = df_item["item_type"].iloc[0]
    average_price = df_item["average_price"].iloc[0]
    my_quantity = df_item["my_quantity"].iloc[0]

    df_clean = df_item[~df_item["is_suspected_anchor"]].copy() if "is_suspected_anchor" in df_item.columns else df_item.copy()
    if df_clean.empty:
        df_clean = df_item.copy()

    total_qty_clean = float(df_clean["quantity"].sum())
    total_listings_clean = int(len(df_clean))

    level = df_clean.groupby("price", as_index=False)["quantity"].sum().rename(columns={"quantity": "level_qty"})
    max_level_share_clean = float(level["level_qty"].max()) / total_qty_clean

    exclusive_mode = (total_qty_clean <= EXCLUSIVE_TOTAL_UNITS_THRESHOLD) or (max_level_share_clean >= EXCLUSIVE_DOMINANCE_SHARE)

    if exclusive_mode:
        fair_price = _unweighted_price_quantile(df_clean, 0.5)
        q1_price = _unweighted_price_quantile(df_clean, 0.25)
        q3_price = _unweighted_price_quantile(df_clean, 0.75)
    else:
        fair_price = _weighted_price_quantile(df_clean, 0.5)
        q1_price = _weighted_price_quantile(df_clean, 0.25)
        q3_price = _weighted_price_quantile(df_clean, 0.75)

    # Buyers take the cheapest listing first, so the only price that sells
    # "fast" is one dollar under the current clean floor. Anchor removal only
    # catches high outliers; a fat-fingered $1 listing would otherwise drag
    # every undercut down with it, so listings far below the median don't
    # count as the floor.
    sane = df_clean.loc[df_clean["price"] >= LOW_OUTLIER_FACTOR * fair_price, "price"]
    floor_price = float(sane.min()) if not sane.empty else float(fair_price)
    fast_sell_price = max(math.floor(floor_price) - 1, 1.0)

    payload: dict = build_stat_payload(df_item)

    result: dict = {
        "item_id": item_id,
        "item_name": item_name,
        "item_type": item_type,
        "average_price_reported": average_price,
        "my_quantity": my_quantity,
        "num_listings": int(len(df_item)),
        "num_suspected_anchors": int(df_item["is_suspected_anchor"].sum()) if "is_suspected_anchor" in df_item.columns else 0,
        "num_clean_listings": total_listings_clean,
        "floor_price": floor_price,
        "fast_sell_price": float(fast_sell_price),
        "fair_price": float(fair_price),
        "greedy_price": float(q3_price),
        "clean_median_price": float(fair_price),
        "clean_q1_price": float(q1_price),
        "clean_q3_price": float(q3_price),
        "copy_tool": payload,
    }
    return result


def enrich_all_items(long_df: pd.DataFrame) -> pd.DataFrame:
    return long_df.groupby("item_id", group_keys=False).apply(enrich_item_orders).reset_index(drop=True)


def build_summary_from_enriched(enriched_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([compute_price_suggestions_for_item(df_item) for _, df_item in enriched_df.groupby("item_id")])


def _weighted_median_price(df: pd.DataFrame) -> float:
    d = df.sort_values("price")
    return _weighted_median(d["price"].to_numpy(dtype=float), d["quantity"].to_numpy(dtype=float))


def _quality_bucket(points: list[dict], q: float) -> dict:
    if not points:
        return {}
    if q <= points[0]["q"]:
        return points[0]
    if q >= points[-1]["q"]:
        return points[-1]
    for a, b in zip(points, points[1:]):
        if a["q"] <= q <= b["q"]:
            if b["q"] == a["q"]:
                return b
            t = (q - a["q"]) / (b["q"] - a["q"])
            return {k: a[k] + (b[k] - a[k]) * t for k in ("fast", "fair", "greedy")}
    return points[-1]


def price_at_quality(payload: dict, quality: float) -> dict:
    pts = payload.get("points") or []
    if not pts:
        return {"fast": None, "fair": None, "greedy": None, "derived": False}
    bucket = _quality_bucket(pts, quality)
    return {
        "fast": bucket.get("fast"),
        "fair": bucket.get("fair"),
        "greedy": bucket.get("greedy"),
        "derived": True,
    }


def quality_from_stats(payload: dict, stats: dict) -> float | None:
    if not stats:
        return None
    fit = payload.get("fit") or {}
    stype = fit.get("type")
    base = fit.get("base") or {}
    coef = fit.get("coef") or {}

    if stype == "Weapon":
        dmg = stats.get("damage")
        acc = stats.get("accuracy")
        if dmg is None and acc is None:
            return None
        try:
            q = float(fit.get("intercept") or 0.0)
            if dmg is not None:
                q += float(coef.get("damage") or 0.0) * (float(dmg) - float(base.get("damage") or 0.0))
            if acc is not None:
                q += float(coef.get("accuracy") or 0.0) * (float(acc) - float(base.get("accuracy") or 0.0))
            return q
        except (TypeError, ValueError):
            return None
    if stype == "Armor":
        arm = stats.get("armor")
        if arm is None:
            return None
        try:
            if fit.get("intercept") is not None:
                return float(fit["intercept"]) + float(coef.get("armor") or 0.0) * (float(arm) - float(base.get("armor") or 0.0))
        except (TypeError, ValueError):
            pass
        try:
            return (float(arm) - float(base.get("armor") or 0.0)) * 20.0
        except (TypeError, ValueError):
            return None
    return None


def _median_stat(df_q: pd.DataFrame, col: str) -> float | None:
    idx = df_q[col].dropna()
    if idx.empty:
        return None
    return float(np.median(idx.to_numpy(dtype=float)))


def build_stat_payload(df_item: pd.DataFrame) -> dict:
    df_item = df_item.copy()
    if "listing_quality" not in df_item.columns:
        return _empty_payload()

    df_q = df_item[df_item["listing_quality"].notna()].copy()
    if df_q.empty:
        return _empty_payload()

    points: list[dict] = []
    df_q = df_q.copy()
    df_q["bucket"] = (df_q["listing_quality"] / QUALITY_STEP).round() * QUALITY_STEP
    for bucket, grp in df_q.groupby("bucket"):
        if grp.empty:
            continue
        # Prices and their quantities must be sorted together, or each price
        # gets another listing's weight.
        by_price = grp.sort_values("price")
        prices = by_price["price"].to_numpy(dtype=float)
        weights = by_price["quantity"].to_numpy(dtype=float)
        fast = _weighted_quantile(prices, weights, 0.25)
        fair = _weighted_median_price(grp)
        greedy = _weighted_quantile(prices, weights, 0.75)
        points.append({"q": float(bucket), "n": int(len(grp)), "fast": fast, "fair": fair, "greedy": greedy})
    points.sort(key=lambda p: p["q"])

    median_quality = float(np.median(df_q["listing_quality"].to_numpy(dtype=float)))
    median_stats = {
        "damage": _median_stat(df_q, "listing_damage"),
        "accuracy": _median_stat(df_q, "listing_accuracy"),
        "armor": _median_stat(df_q, "listing_armor"),
    }

    has_damage = df_q["listing_damage"].notna().sum()
    has_accuracy = df_q["listing_accuracy"].notna().sum()
    has_armor = df_q["listing_armor"].notna().sum()
    base: dict = {}
    fit: dict = {"type": None, "intercept": None, "coef": {}, "base": {}, "n": 0}

    if has_damage >= MIN_FIT_SAMPLES or has_accuracy >= MIN_FIT_SAMPLES:
        fit["type"] = "Weapon"
        base = {"damage": _median_stat(df_q, "listing_damage"), "accuracy": _median_stat(df_q, "listing_accuracy")}
        fit["base"] = base
        _fit_weapon_quality(df_q, fit)
    elif has_armor >= MIN_FIT_SAMPLES:
        fit["type"] = "Armor"
        base = {"armor": _median_stat(df_q, "listing_armor")}
        fit["base"] = base
        _fit_armor_quality(df_q, fit)

    return {
        "stat_type": fit["type"],
        "points": points,
        "median_quality": median_quality,
        "median_stats": median_stats,
        "fit": fit,
        "n_quality_listings": int(len(df_q)),
    }


def _fit_weapon_quality(df_q: pd.DataFrame, fit: dict) -> None:
    dmg = df_q["listing_damage"].dropna()
    acc = df_q["listing_accuracy"].dropna()
    mask = df_q["listing_damage"].notna() & df_q["listing_accuracy"].notna()
    d = df_q[mask]
    if len(d) < MIN_FIT_SAMPLES:
        return
    X = []
    y = []
    for _, r in d.iterrows():
        dev_d = float(r["listing_damage"]) - float(fit["base"]["damage"])
        dev_a = float(r["listing_accuracy"]) - float(fit["base"]["accuracy"])
        X.append([1.0, dev_d, dev_a])
        y.append(float(r["listing_quality"]))
    fit["n"] = len(y)
    _run_fit(X, y, fit, keys=("intercept", "damage", "accuracy"))


def _fit_armor_quality(df_q: pd.DataFrame, fit: dict) -> None:
    d = df_q[df_q["listing_armor"].notna()]
    if len(d) < MIN_FIT_SAMPLES:
        return
    X = []
    y = []
    for _, r in d.iterrows():
        dev = float(r["listing_armor"]) - float(fit["base"]["armor"])
        X.append([1.0, dev])
        y.append(float(r["listing_quality"]))
    fit["n"] = len(y)
    _run_fit(X, y, fit, keys=("intercept", "armor"))


def _run_fit(X: list, y: list, fit: dict, keys: tuple) -> None:
    Xa = np.array(X, dtype=float)
    ya = np.array(y, dtype=float)
    coefs, *_ = np.linalg.lstsq(Xa, ya, rcond=None)
    fit["coef"] = dict(zip(keys[1:], coefs[1:].tolist()))
    fit["intercept"] = float(coefs[0])


def _empty_payload() -> dict:
    return {
        "stat_type": None,
        "points": [],
        "median_quality": None,
        "median_stats": None,
        "fit": {"type": None, "intercept": None, "coef": {}, "base": {}},
        "n_quality_listings": 0,
    }