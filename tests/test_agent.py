from pathlib import Path
import os
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent13 import protocol
from agent13.native import Backend
from agent13.service import Agent, peer_allowed
from test_protocol import request


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent13-tokens-")
        self.root = Path(self.temp.name)
        self.file = self.root / "sample"
        self.file.write_bytes(b"safe test data")
        self.file.chmod(0o600)
        self.backend = Backend(self.root)
        self.now = 100.0
        self.agent = Agent(self.backend, token_ttl=5, max_tokens=2, clock=lambda: self.now)

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def call(self, action, **params):
        return self.agent.handle(protocol.encode(request(action, **params)))

    def preview(self):
        result = self.call("preview_shred", path="sample")
        self.assertTrue(result["success"], result)
        return result["data"]["confirm_token"]

    def test_expired_token(self):
        token = self.preview()
        self.now += 5
        result = self.call("execute_shred", path="sample", confirm_token=token)
        self.assertEqual(result["error"]["code"], "EXPIRED_TOKEN")
        self.assertTrue(self.file.exists())

    def test_mismatch_consumes_token(self):
        token = self.preview()
        result = self.call("execute_shred", path="other", confirm_token=token)
        self.assertEqual(result["error"]["code"], "TOKEN_MISMATCH")
        result = self.call("execute_shred", path="sample", confirm_token=token)
        self.assertEqual(result["error"]["code"], "INVALID_TOKEN")
        self.assertTrue(self.file.exists())

    def test_token_capacity_and_expiry_cleanup(self):
        self.preview(); self.preview()
        result = self.call("preview_shred", path="sample")
        self.assertEqual(result["error"]["code"], "TOKEN_LIMIT")
        self.now += 6
        self.preview()
        self.assertEqual(len(self.agent.tokens), 1)

    def test_file_limit(self):
        self.agent.max_bytes = 0
        result = self.call("preview_shred", path="sample")
        self.assertEqual(result["error"]["code"], "FILE_TOO_LARGE")
        self.assertTrue(self.file.exists())

    def test_replacement_and_metadata_changes(self):
        for change in ("replace", "append", "chmod"):
            with self.subTest(change=change):
                self.file.chmod(0o600)
                token = self.preview()
                if change == "replace":
                    replacement = self.root / "new"
                    replacement.write_bytes(b"new data"); replacement.chmod(0o600)
                    replacement.replace(self.file)
                elif change == "append":
                    with self.file.open("ab") as stream:
                        stream.write(b"changed")
                else:
                    self.file.chmod(0o640)
                current = self.file.read_bytes()
                result = self.call("execute_shred", path="sample", confirm_token=token)
                self.assertEqual(result["error"]["code"], "FILE_CHANGED")
                self.assertEqual(self.file.read_bytes(), current)

    def test_peer_uid_policy(self):
        connection = Mock()
        with patch("agent13.service.os.geteuid", return_value=1000):
            connection.getsockopt.return_value = struct.pack("3i", 123, 1001, 1001)
            self.assertFalse(peer_allowed(connection))
            connection.getsockopt.return_value = struct.pack("3i", 123, 1000, 1000)
            self.assertTrue(peer_allowed(connection))

    def test_links_created_after_preview_preserve_other_file(self):
        other = self.root / "other"
        other.write_bytes(b"must survive"); other.chmod(0o600)
        token = self.preview()
        self.file.unlink()
        self.file.symlink_to(other)
        result = self.call("execute_shred", path="sample", confirm_token=token)
        self.assertEqual(result["error"]["code"], "UNSAFE_TARGET")
        self.assertEqual(other.read_bytes(), b"must survive")
        self.file.unlink()
        self.file.write_bytes(b"safe sample"); self.file.chmod(0o600)
        token = self.preview()
        os.link(self.file, self.root / "hard")
        result = self.call("execute_shred", path="sample", confirm_token=token)
        self.assertEqual(result["error"]["code"], "UNSAFE_TARGET")
        self.assertEqual(self.file.read_bytes(), b"safe sample")
