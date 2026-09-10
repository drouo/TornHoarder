import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tma.http_client import TokenBucket, api_error, cooldown_seconds, envelope, get_json_with_retry


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is _NOT_JSON:
            raise ValueError("not json")
        return self._payload


_NOT_JSON = object()


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls += 1
        return self._responses.pop(0)


class ApiErrorTest(unittest.TestCase):
    def test_dict_error(self):
        self.assertEqual(api_error({"error": {"code": 2, "error": "Incorrect key"}}), "2 Incorrect key")

    def test_no_error(self):
        self.assertIsNone(api_error({"data": {}}))


class EnvelopeTest(unittest.TestCase):
    def test_unwraps_data_key(self):
        self.assertEqual(envelope({"data": {"a": 1}}), {"a": 1})

    def test_passthrough_when_flat(self):
        self.assertEqual(envelope({"a": 1}), {"a": 1})


class CooldownSecondsTest(unittest.TestCase):
    def test_parses_reset(self):
        self.assertEqual(cooldown_seconds("Too many calls. Resets at 0:00:20", default=99), 21.0)

    def test_default_when_unparseable(self):
        self.assertEqual(cooldown_seconds("nope", default=7), 7.0)


class TokenBucketTest(unittest.TestCase):
    def test_take_does_not_block_within_capacity(self):
        bucket = TokenBucket(rate_per_min=600, capacity=5)
        for _ in range(5):
            bucket.take(1)
        self.assertLessEqual(bucket.tokens, 0.01)


class GetJsonWithRetryTest(unittest.TestCase):
    def test_success_first_try(self):
        session = FakeSession([FakeResponse(200, {"ok": True})])
        data, err = get_json_with_retry(session, None, "http://x", "key", retries=3)
        self.assertEqual(data, {"ok": True})
        self.assertIsNone(err)
        self.assertEqual(session.calls, 1)

    def test_retries_on_429_then_succeeds(self):
        session = FakeSession(
            [
                FakeResponse(429, {"error": {"code": 5, "error": "Too many requests"}}),
                FakeResponse(200, {"ok": True}),
            ]
        )
        with patch("time.sleep", return_value=None):
            data, err = get_json_with_retry(session, None, "http://x", "key", retries=3)
        self.assertEqual(data, {"ok": True})
        self.assertIsNone(err)

    def test_non_transient_error_stops_immediately(self):
        session = FakeSession([FakeResponse(200, {"error": {"code": 2, "error": "Incorrect key"}})])
        data, err = get_json_with_retry(session, None, "http://x", "key", retries=3)
        self.assertIsNone(data)
        self.assertEqual(err, "2 Incorrect key")
        self.assertEqual(session.calls, 1)

    def test_exhausts_retries_on_repeated_5xx(self):
        session = FakeSession(
            [FakeResponse(503, {"error": {"code": 0, "error": "temporary"}}) for _ in range(3)]
        )
        with patch("time.sleep", return_value=None):
            data, err = get_json_with_retry(session, None, "http://x", "key", retries=3)
        self.assertIsNone(data)
        self.assertEqual(session.calls, 3)


if __name__ == "__main__":
    unittest.main()
