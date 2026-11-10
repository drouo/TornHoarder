import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from tma.catalog import fetch_items, write_catalog  # noqa: E402
from tma.http_client import session_for_requests  # noqa: E402


def main():
    api_key = input("Enter Torn API key: ").strip()
    if not api_key:
        raise SystemExit("API key is empty.")

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    items, err = fetch_items(session_for_requests(), api_key)
    if err is not None:
        raise RuntimeError(err)

    (out_dir / "torn_items_v2.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_catalog(items, out_dir / "torn_item_dictionary.csv", out_dir / "torn_item_profiles.json")

    print(f"Saved in: {out_dir}")


if __name__ == "__main__":
    main()
