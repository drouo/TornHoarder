import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self._tmpdir.name) / ".tma_config.json"

    def tearDown(self):
        config.CONFIG_PATH = self._orig_path
        self._tmpdir.cleanup()

    def test_load_config_missing_file_returns_empty(self):
        self.assertEqual(config.load_config(), {})

    def test_save_and_load_api_key(self):
        config.save_api_key("abc123")
        self.assertEqual(config.load_api_key(), "abc123")

    def test_clear_api_key(self):
        config.save_api_key("abc123")
        config.clear_api_key()
        self.assertEqual(config.load_api_key(), "")

    def test_clear_api_key_when_no_config_is_a_noop(self):
        config.clear_api_key()
        self.assertEqual(config.load_api_key(), "")

    def test_load_config_corrupt_json_returns_empty(self):
        config.CONFIG_PATH.write_text("not json", encoding="utf-8")
        self.assertEqual(config.load_config(), {})


if __name__ == "__main__":
    unittest.main()
