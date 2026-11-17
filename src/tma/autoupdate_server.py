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
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .foreign_stock import recent_restocks
from .shops import record_shop_report
from .trips import record_buy

DEFAULT_PORT = 9999
SCRIPT_NAME = "tornhoarder.user.js"
MAX_REPORT_BYTES = 200_000


def running_path(script_path: Path) -> Path:
    return script_path.with_name(script_path.name + ".running")


def last_running(script_path: Path) -> Optional[dict]:
    """{"version", "seen"}: the build the script last reported from a Torn
    page, or None. A download alone proves nothing (Tampermonkey's update
    checks download it too); this is what's actually running."""
    try:
        got = json.loads(running_path(script_path).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) and got.get("version") else None
    except (OSError, ValueError):
        return None


def log_path(script_path: Path) -> Path:
    """The script's diagnostics ("[TornHoarder] ..." lines), one JSON per line."""
    return script_path.with_name("tornhoarder.log.jsonl")


MAX_LOG_BYTES = 2_000_000


def _append_log(script_path: Path, entry: dict) -> None:
    path = log_path(script_path)
    line = json.dumps({"ts": int(time.time()), **{k: str(entry.get(k, ""))[:4000] for k in ("ver", "page", "msg")}})
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:  # keep the newer half
        text = path.read_text(encoding="utf-8")
        path.write_text(text[len(text) // 2:].split("\n", 1)[-1], encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _make_handler(
    script_path: Path, reports_path: Optional[Path], status_path: Optional[Path] = None,
    travel_path: Optional[Path] = None, plan_path: Optional[Path] = None,
    trips_path: Optional[Path] = None, foreign_path: Optional[Path] = None, trade_path: Optional[Path] = None,
) -> type[http.server.BaseHTTPRequestHandler]:
    # JSON the app writes for the userscript to fetch, with what to serve before it exists.
    feeds = {
        "/stock-status": (status_path, b'{"checked": 0, "short": []}'),  # Keep stocked badge
        "/travel-prices": (travel_path, b'{"checked": 0, "sell": {}}'),  # buying abroad
        "/shop-plan": (plan_path, b'{"checked": 0, "route": []}'),  # the day's city-shop buys
        "/trade-plan": (trade_path, b'{"checked": 0, "traders": [], "pull": []}'),  # what to give each trader
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            url = urlsplit(self.path)
            if url.path == "/hello":  # each Torn page load: which build is running
                version = (parse_qs(url.query).get("v") or [""])[0][:40]
                if version:
                    try:
                        running_path(script_path).write_text(
                            json.dumps({"version": version, "seen": int(time.time())}), encoding="utf-8")
                    except OSError:
                        pass
                self.send_response(204)
                self.end_headers()
                return
            if self.path in feeds or self.path == "/restocks":
                if self.path == "/restocks":  # restock alerts, from the foreign stock log
                    found = recent_restocks(foreign_path, time.time()) if foreign_path else []
                    body = json.dumps({"checked": int(time.time()), "restocks": found}).encode()
                else:
                    path, empty = feeds[self.path]
                    body = path.read_bytes() if path and path.exists() else empty
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-cache, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
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
            # /shop-report: a city shop's Sell page; /trip-buy: a buy abroad; /script-log: diagnostics.
            sinks = {"/shop-report": (reports_path, record_shop_report), "/trip-buy": (trips_path, record_buy),
                     "/script-log": (script_path, _append_log)}
            path, record = sinks.get(self.path, (None, None))
            if path is None:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_REPORT_BYTES:
                self.send_error(413)
                return
            try:
                record(path, json.loads(self.rfile.read(length)))
            except (ValueError, TypeError, KeyError):
                self.send_error(400)
                return
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # silence request logs

    return Handler


def build_server(
    script_path: Path, port: int = DEFAULT_PORT, reports_path: Optional[Path] = None,
    status_path: Optional[Path] = None, travel_path: Optional[Path] = None, plan_path: Optional[Path] = None,
    trips_path: Optional[Path] = None, foreign_path: Optional[Path] = None, trade_path: Optional[Path] = None,
) -> http.server.HTTPServer:
    """Build (but don't start) the update server. Raises OSError if the port is taken."""
    return http.server.HTTPServer(("127.0.0.1", port), _make_handler(
        script_path, reports_path, status_path, travel_path, plan_path, trips_path, foreign_path, trade_path))
