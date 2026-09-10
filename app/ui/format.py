import pandas as pd


def fmt_int(x) -> str:
    if pd.isna(x):
        return ""
    try:
        return f"{int(x):,}"
    except Exception:
        return str(x)
