"""A code update rebuilds the served script from the last Run's inputs, and
the running script reports its build, so the app can say whether an update took."""

import json
import threading
import urllib.request

import pytest

from app.ui import autofill_export
from tma.app_status import script_running
from tma.autoupdate_server import build_server, last_running, log_path
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


def test_the_server_notes_the_build_running_in_torn(tmp_path):
    script = tmp_path / "tornhoarder.user.js"
    script.write_text("// script", encoding="utf-8")
    server = build_server(script, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert urllib.request.urlopen(base + "/tornhoarder.user.js", timeout=5).read() == b"// script"
        assert last_running(script) is None  # a download (e.g. an update check) isn't a running script
        assert urllib.request.urlopen(base + "/hello?v=1.5.1.1794852228", timeout=5).status == 204
        req = urllib.request.Request(base + "/script-log", method="POST", headers={"Content-Type": "application/json"},
                                     data=b'{"ver": "1.5.1.1", "page": "/page.php?sid=travel", "msg": "[TornHoarder] abroad: x"}')
        assert urllib.request.urlopen(req, timeout=5).status == 204
    finally:
        server.shutdown()
    logged = log_path(script).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["msg"] for line in logged] == ["[TornHoarder] abroad: x"]
    got = last_running(script)
    assert got["version"] == "1.5.1.1794852228"
    assert got["seen"] == pytest.approx(script.stat().st_mtime, abs=120)


def test_the_sidebar_says_whether_the_newest_build_runs():
    now = 1_794_900_000
    assert script_running("1.5.1.1794852228", None, now)[0] == "unknown"
    level, text = script_running("1.5.1.1794852228", {"version": "1.5.1.1794852228", "seen": now - 60}, now)
    assert (level, text.startswith("✓ Torn is running the newest build")) == ("ok", True)
    level, text = script_running("1.5.1.1794852228", {"version": "1.5.1.1794800000", "seen": now - 60}, now)
    assert level == "old" and "Install / update" in text
    assert "an older build" in script_running("1.5.1.1794852228", {"version": "1.5.1", "seen": now}, now)[1]
