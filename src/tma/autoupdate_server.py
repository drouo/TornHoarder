"""Tiny HTTP server that serves the generated tornhoarder.user.js so
Tampermonkey can install/update it from a URL instead of a downloaded file.

It also accepts `POST /shop-report` from the userscript (sent through
Tampermonkey's GM_xmlhttpRequest) with what a city shop's Sell page listed,
so the app learns which shop really buys which item.

`build_server()` only constructs the server; the caller runs it (normally
on a background thread — see app/ui/autofill_export.py, which starts one
automatically inside the Streamlit process). `tools/update_server.py`
remains a thin CLI wrapper around the same server for anyone who wants to
run it standalone.
"""

from __future__ import annotations

import http.server
import json
from pathlib import Path
from typing import Optional

from .shops import record_shop_report

DEFAULT_PORT = 9999
SCRIPT_NAME = "tornhoarder.user.js"
MAX_REPORT_BYTES = 200_000


def _make_handler(script_path: Path, reports_path: Optional[Path]) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            if not script_path.exists():
                self.send_error(404, "script not yet generated")
                return
            content = script_path.read_bytes()
            self.send_response(200)
            # text/plain, not application/javascript: Tampermonkey's install-
            # detection only reliably intercepts .user.js navigations served
            # as plain text (this is what GreasyFork and other userscript
            # hosts use) — application/javascript makes Chrome just render
            # the source as a script-viewer page instead of handing it to
            # the extension.
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, max-age=0")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self) -> None:  # noqa: N802 (stdlib method name)
            if self.path != "/shop-report" or reports_path is None:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_REPORT_BYTES:
                self.send_error(413)
                return
            try:
                record_shop_report(reports_path, json.loads(self.rfile.read(length)))
            except (ValueError, TypeError):
                self.send_error(400)
                return
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence request logs

    return Handler


def build_server(
    script_path: Path, port: int = DEFAULT_PORT, reports_path: Optional[Path] = None
) -> http.server.HTTPServer:
    """Build (but don't start) the update server. Raises OSError if the port is taken."""
    return http.server.HTTPServer(("127.0.0.1", port), _make_handler(script_path, reports_path))
