import os
import fcntl
from pathlib import Path
import tempfile
import unittest

from agent13.native import Backend, NativeError


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent13-wrapper-")
        self.root = Path(self.temp.name)
        self.backend = Backend(self.root)
        self.sample = self.root / "sample"
        self.sample.write_bytes(b"disposable data" * 100)
        self.sample.chmod(0o600)

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def test_inspect_is_dry_and_shred_calls_c(self):
        before = self.sample.read_bytes()
        info = self.backend.inspect(str(self.sample))
        self.assertEqual(info.size, len(before))
        self.assertEqual(self.sample.read_bytes(), before)
        result = self.backend.shred("sample", info)
        self.assertEqual(result["bytes_written"], len(before))
        self.assertTrue(result["unlinked"])
        self.assertFalse(self.sample.exists())

    def test_changed_file(self):
        info = self.backend.inspect("sample")
        self.sample.write_bytes(b"replacement")
        with self.assertRaises(NativeError) as caught:
            self.backend.shred("sample", info)
        self.assertEqual(caught.exception.code, "FILE_CHANGED")
        self.assertEqual(self.sample.read_bytes(), b"replacement")

    def test_rejections(self):
        (self.root / "dir").mkdir()
        (self.root / "sym").symlink_to(self.sample)
        os.mkfifo(self.root / "fifo")
        for path in ("missing", "dir", "sym", "fifo", "../sample", "/etc/passwd",
                     "a/../sample", "./sample", "sample/", "a//b", ".agent13-hidden"):
            with self.subTest(path=path), self.assertRaises(NativeError):
                self.backend.inspect(path)
        os.link(self.sample, self.root / "hard")
        with self.assertRaises(NativeError):
            self.backend.inspect("sample")

    def test_permissions_and_parent_symlink(self):
        self.sample.chmod(0o400)
        with self.assertRaises(NativeError) as caught:
            self.backend.inspect("sample")
        self.assertEqual(caught.exception.details["errno"], 13)
        (self.root / "parent-link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(NativeError):
            self.backend.inspect("parent-link/sample")

    def test_nested_and_empty(self):
        (self.root / "sub").mkdir(mode=0o700)
        (self.root / "sub/empty").touch(mode=0o600)
        info = self.backend.inspect("sub/empty")
        self.assertEqual(self.backend.shred("sub/empty", info)["bytes_written"], 0)

    def test_insecure_root_and_missing_library(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            Backend(self.root)
        self.root.chmod(0o700)
        with self.assertRaises(OSError):
            Backend(self.root, self.root / "missing.so")

    def test_busy_file_and_shared_parent(self):
        info = self.backend.inspect("sample")
        with self.sample.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(NativeError) as caught:
                self.backend.shred("sample", info)
            self.assertEqual(caught.exception.code, "FILE_BUSY")
        self.assertTrue(self.sample.exists())
        shared = self.root / "shared"
        shared.mkdir(mode=0o700)
        (shared / "file").touch(mode=0o600)
        shared.chmod(0o777)
        with self.assertRaises(NativeError):
            self.backend.inspect("shared/file")


if __name__ == "__main__":
    unittest.main()
