import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import collector


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if size == -1:
            return self.body
        return self.body[:size]


class CollectorSecurityTests(unittest.TestCase):
    def test_fetch_usage_rejects_oversized_response(self):
        body = b"{" + b"a" * (collector.MAX_RESPONSE_BYTES + 1) + b"}"
        with mock.patch("collector.urllib.request.urlopen", return_value=FakeResponse(body)):
            payload, status = collector.fetch_usage("https://example.test", "secret")
        self.assertIsNone(payload)
        self.assertEqual(status, "error")

    def test_add_key_reads_secret_from_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            collector.KEYS_PATH = Path(directory) / "keys.json"
            with mock.patch("sys.stdin", io.StringIO("afk-secret\n")):
                self.assertTrue(collector.keys_cli(["add-key", "work", "--stdin"]))
            self.assertEqual(collector.load_keys(), [{"label": "work", "key": "afk-secret"}])

    def test_save_keys_does_not_follow_tmp_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector.KEYS_PATH = root / "keys.json"
            target = root / "victim"
            target.write_text("keep")
            (root / "keys.json.tmp").symlink_to(target)
            with self.assertRaises(OSError):
                collector.save_keys([])
            self.assertEqual(target.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
