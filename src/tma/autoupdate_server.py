"""Tiny HTTP server that serves the generated tma_autofill.user.js so
Tampermonkey can install/update it from a URL instead of a downloaded file.

`build_server()` only constructs the server; the caller runs it (normally
on a background thread — see app/ui/autofill_export.py, which starts one
automatically inside the Streamlit process). `tools/update_server.py`
remains a thin CLI wrapper around the same server for anyone who wants to
run it standalone.
"""

from __future__ import annotations

import http.server
from pathlib import Path

DEFAULT_PORT = 9999
SCRIPT_NAME = "tma_autofill.user.js"


def _make_handler(script_path: Path) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
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

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence request logs

    return Handler


def build_server(script_path: Path, port: int = DEFAULT_PORT) -> http.server.HTTPServer:
    """Build (but don't start) the update server. Raises OSError if the port is taken."""
    return http.server.HTTPServer(("127.0.0.1", port), _make_handler(script_path))
