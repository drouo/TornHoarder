"""Human-sized money: $950, $1.2k, $474k, $15.6m, $2.95b."""

from __future__ import annotations

_UNITS = ((1e12, "t"), (1e9, "b"), (1e6, "m"), (1e3, "k"))


def _sig3(n: float) -> str:
    text = f"{n:.3g}"
    return f"{n:.0f}" if "e" in text else text


def money(v) -> str:
    """Three significant figures with a k/m/b/t suffix; under $1k it's exact."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x != x:
        return ""
    sign, x = ("-" if x < 0 else ""), abs(x)
    for i, (scale, unit) in enumerate(_UNITS):
        if x >= scale:
            n = x / scale
            if n >= 999.5 and i > 0:  # 999,950 -> $1m, not $1000k
                n, unit = n / 1000, _UNITS[i - 1][1]
            return f"{sign}${_sig3(n)}{unit}"
    if x >= 999.5:
        return f"{sign}$1k"
    return f"{sign}${x:,.0f}"
