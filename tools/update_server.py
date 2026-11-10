"""Minimal HTTP server that serves the latest tma_autofill.user.js for Tampermonkey auto-update.

Usage:
    python tools/update_server.py [--port 9999]

Tampermonkey @updateURL: http://127.0.0.1:9999/tma_autofill.user.js

Note: the Streamlit app now starts this same server automatically inside
its own process (see app/ui/autofill_export.py) as soon as the Autofill
tab runs, so running this script by hand is only needed if you want the
update server available without the app itself running.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tma.autoupdate_server import DEFAULT_PORT, SCRIPT_NAME, build_server

DATA_DIR = ROOT / "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    server = build_server(DATA_DIR / SCRIPT_NAME, port=args.port)
    print(f"Update server listening on http://127.0.0.1:{args.port}/{SCRIPT_NAME}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
