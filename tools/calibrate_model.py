from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tma.item_stats import StatValueModel, load_profiles, sidecar

PROFILES_PATH = ROOT / "data" / "torn_item_profiles.json"


def main() -> None:
    profiles = load_profiles(PROFILES_PATH)
    model = StatValueModel()
    model.fit(profiles)

    print(f"{'model':<14} {'n':>4} {'R2':>7}  coefs")
    for key, data in sorted(model.models.items()):
        print(f"{key:<14} {data['n']:>4} {data['r2']:>7.3f}  " + " ".join(f"{c}:{v:.4g}" for c, v in zip(data["features"], data["coef"][1:])))

    fitted = 0
    tier_counts: dict = {}
    examples = []
    for pid, prof in profiles.items():
        info = sidecar(model, profiles, int(pid))
        if info.implied_value is None:
            continue
        fitted += 1
        tier_counts[info.tier] = tier_counts.get(info.tier, 0) + 1
        mp = info.market_price or 0.0
        scale = info.implied_value / mp if mp > 0 else 0.0
        if len(examples) < 8 or scale >= 2.0:
            examples.append((info.tier, prof["name"], mp, info.implied_value, scale))

    print(f"\nfitted stat items: {fitted}")
    for tier, n in sorted(tier_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {tier:<8} {n}")
    print("\nexamples (tier, name, market_price, implied, implied/market):")
    for t, name, mp, iv, s in sorted(examples, key=lambda e: -e[4]):
        print(f"  {t:<9} {name:<35} {mp:>10,.0f} {iv:>12,.0f} x{s:.2f}")


if __name__ == "__main__":
    main()