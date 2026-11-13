"""Log foreign shop stock from YATA's travel feed, every few minutes, for the
trip planner's "will it be in stock when I land?" odds.

Usage:
    python tools/log_foreign_stock.py [--interval 300] [--once] [--log FILE]

Only one copy runs at a time (data/foreign_logger.lock); a second one exits.
At login it's started by a shortcut in the Windows Startup folder that runs
it with pythonw (no window) and --log data/foreign_logger.log.

Writes data/foreign_stock.csv (changes only) and data/foreign_updates.csv.
Leave it running while your PC is on; it picks up where it left off.
"""

import argparse
import sys
import time
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
    parser.add_argument("--log", type=Path, help="append messages to this file instead of printing them")
    args = parser.parse_args()

    def say(message: str) -> None:
        if args.log:
            with args.log.open("a", encoding="utf-8") as f:
                f.write(message + "\n")
        else:
            print(message, flush=True)

    lock = None if args.once else _single_instance(DATA_DIR / "foreign_logger.lock")
    if lock is False:
        say(f"{time.strftime('%Y-%m-%d %H:%M:%S')} another logger is already running; exiting")
        return
    log = StockLog(DATA_DIR / "foreign_stock.csv", DATA_DIR / "foreign_updates.csv")
    say(f"{time.strftime('%Y-%m-%d %H:%M:%S')} logging YATA foreign stock every {max(args.interval, 60)}s to {DATA_DIR}")
    run(log, session_for_requests(), interval_s=max(args.interval, 60), once=args.once, say=say)


def _single_instance(path: Path):
    """Hold an exclusive lock on `path` for the life of this process; False if
    another process holds it. Windows releases it when the process exits."""
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return False
    return f


if __name__ == "__main__":
    main()
