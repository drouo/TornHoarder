import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.autoupdate_server import build_server


class AutoupdateServerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.script_path = Path(self._tmpdir.name) / "tma_autofill.user.js"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _serve(self):
        server = build_server(self.script_path, port=0)  # port 0 -> OS picks a free port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return server

    def test_404_before_script_exists(self):
        server = self._serve()
        port = server.server_address[1]
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_serves_script_content(self):
        self.script_path.write_text("// tma userscript", encoding="utf-8")
        server = self._serve()
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read().decode("utf-8"), "// tma userscript")
            self.assertEqual(resp.headers["Content-Type"], "text/plain; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
