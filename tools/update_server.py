"""Minimal HTTP server that serves the latest tma_autofill.user.js for Tampermonkey auto-update.

Usage:
    python tools/update_server.py [--port 9999]

Tampermonkey @updateURL: http://localhost:9999/tma_autofill.user.js
"""

import argparse
import http.server
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SCRIPT_NAME = "tma_autofill.user.js"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        script_path = DATA_DIR / SCRIPT_NAME
        if not script_path.exists():
            self.send_error(404, "script not yet generated")
            return
        content = script_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Cache-Control", "no-cache, max-age=0")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass  # silence request logs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    server = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Update server listening on http://127.0.0.1:{args.port}/{SCRIPT_NAME}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
