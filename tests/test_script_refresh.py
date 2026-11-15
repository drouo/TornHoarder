"""A code update rebuilds the served script from the last Run's inputs, and
the server notes when Tampermonkey last took it, so fixes aren't missed."""

import threading
import urllib.request

import pytest

from app.ui import autofill_export
from tma.autoupdate_server import build_server, last_fetched
from tma.userscript import TEMPLATE_HASH, template_of

CFG = {"dumpUndercutPct": 1, "topMinQuality": 90, "keepCash": 0, "ipecacOver": 0}


def test_a_built_script_carries_the_template_stamp():
    js, error = autofill_export.write_userscript({1: {"name": "Thing", "fast": 1, "fair": 2, "greedy": 3}}, {}, CFG, None)
    assert error is None
    assert template_of(js) == TEMPLATE_HASH
    assert autofill_export.refresh_userscript() == "current"


def test_older_code_is_rebuilt_from_the_saved_inputs():
    autofill_export.write_userscript({1: {"name": "Thing", "fast": 1, "fair": 2, "greedy": 3}}, {}, CFG, None)
    path = autofill_export.AUTOFILL_PATH
    path.write_text(path.read_text(encoding="utf-8").replace(TEMPLATE_HASH, "oldcode"), encoding="utf-8")
    assert autofill_export.refresh_userscript() == "rebuilt"
    rebuilt = path.read_text(encoding="utf-8")
    assert template_of(rebuilt) == TEMPLATE_HASH
    assert '"1": {"name": "Thing"' in rebuilt  # the last Run's prices survive


def test_a_script_from_before_stamps_needs_one_run():
    autofill_export.AUTOFILL_PATH.write_text("// ==UserScript==\n// @version 1.5.1\n", encoding="utf-8")
    assert autofill_export.refresh_userscript() == "needs-run"
    autofill_export.AUTOFILL_PATH.unlink()
    assert autofill_export.refresh_userscript() == "none"


def test_the_server_notes_when_the_script_is_downloaded(tmp_path):
    script = tmp_path / "tornhoarder.user.js"
    script.write_text("// script", encoding="utf-8")
    assert last_fetched(script) is None
    server = build_server(script, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/tornhoarder.user.js"
        assert urllib.request.urlopen(url, timeout=5).read() == b"// script"
    finally:
        server.shutdown()
    assert last_fetched(script) == pytest.approx(script.stat().st_mtime, abs=120)
