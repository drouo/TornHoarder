"""Log foreign shop stock from YATA's travel feed, every few minutes, for the
trip planner's "will it be in stock when I land?" odds.

Usage:
    python tools/log_foreign_stock.py [--interval 300] [--once]

Writes data/foreign_stock.csv (changes only) and data/foreign_updates.csv.
Leave it running while your PC is on; it picks up where it left off.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tma.foreign_stock import DEFAULT_INTERVAL_S, StockLog, run
from tma.http_client import session_for_requests

DATA_DIR = ROOT / "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S, help="seconds between checks (min 60)")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    args = parser.parse_args()
    log = StockLog(DATA_DIR / "foreign_stock.csv", DATA_DIR / "foreign_updates.csv")
    print(f"Logging YATA foreign stock every {max(args.interval, 60)}s to {DATA_DIR}", flush=True)
    run(log, session_for_requests(), interval_s=max(args.interval, 60), once=args.once,
        say=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
