import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from agent13 import protocol
from agent13.client import call
from test_protocol import request

PROJECT = Path(__file__).resolve().parent.parent


def start_service(root, sock):
    log = tempfile.TemporaryFile(mode="w+")
    process = subprocess.Popen(
        [sys.executable, "-m", "agent13.service", "--allowed-root", str(root),
         "--socket", str(sock)], cwd=PROJECT, stdout=log, stderr=log)
    try:
        for _ in range(250):
            if process.poll() is not None:
                log.seek(0)
                raise RuntimeError(log.read())
            if sock.exists():
                try:
                    if call(sock, "health", timeout=0.2)["success"]:
                        return process, log
                except (OSError, ValueError):
                    pass
            time.sleep(0.01)
        raise RuntimeError("Service did not become ready")
    except BaseException:
        process.terminate()
        process.wait(timeout=5)
        log.close()
        raise


class SocketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent13-ipc-")
        cls.base = Path(cls.temp.name)
        cls.root = cls.base / "allowed"
        cls.root.mkdir(mode=0o700)
        cls.sock = cls.base / "agent13.sock"
        try:
            cls.process, cls.log = start_service(cls.root, cls.sock)
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.seek(0)
        logs = cls.log.read()
        cls.log.close()
        cls.temp.cleanup()
        if cls.process.returncode != 0:
            raise AssertionError(logs)

    def setUp(self):
        self.name = self.id().rsplit(".", 1)[-1]
        self.sample = self.root / self.name
        self.sample.write_bytes(b"disposable sample" * 500)
        self.sample.chmod(0o600)

    def api(self, action, **params):
        return call(self.sock, action, params)

    def cli(self, *args):
        p = subprocess.run([str(PROJECT / "bin/agent13ctl"), "--socket", str(self.sock), *args],
                           cwd=PROJECT, capture_output=True, text=True, timeout=10)
        return p.returncode, json.loads(p.stdout)

    def raw(self, payload):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(self.sock))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            data = bytearray()
            while b"\n" not in data:
                chunk = client.recv(65536)
                if not chunk:
                    break
                data.extend(chunk)
            return json.loads(data)

    def test_health_info_permissions(self):
        self.assertEqual(self.api("health")["data"]["status"], "ok")
        self.assertEqual(self.api("info")["data"]["allowed_root"], str(self.root))
        self.assertEqual(stat.S_IMODE(self.sock.stat().st_mode), 0o600)

    def test_cli_complete_workflow(self):
        original = self.sample.read_bytes()
        status, inspected = self.cli("inspect", str(self.sample))
        self.assertEqual(status, 0)
        self.assertEqual(inspected["data"]["file"]["size"], len(original))
        status, preview = self.cli("preview", str(self.sample))
        self.assertEqual(status, 0)
        self.assertEqual(self.sample.read_bytes(), original)
        token = preview["data"]["confirm_token"]
        status, result = self.cli("shred", self.name, "--confirm-token", token)
        self.assertEqual(status, 0, result)
        self.assertTrue(result["data"]["unlinked"])
        self.assertFalse(self.sample.exists())
        status, result = self.cli("shred", self.name, "--confirm-token", token)
        self.assertEqual(status, 1)
        self.assertEqual(result["error"]["code"], "INVALID_TOKEN")
        self.log.flush()
        # No file path or token should appear in operation logs.
        with open(f"/proc/{self.process.pid}/fd/2") as stream:
            logs = stream.read()
        self.assertNotIn(token, logs)
        self.assertNotIn(self.name, logs)

    def test_cli_defaults_to_dry_run(self):
        for extra in ([], ["--dry-run"]):
            status, result = self.cli("shred", self.name, *extra)
            self.assertEqual(status, 0)
            self.assertTrue(result["data"]["dry_run"])
            self.assertTrue(self.sample.exists())

    def test_missing_permission_directory_symlink_hardlink(self):
        directory = self.root / (self.name + "-dir"); directory.mkdir(mode=0o700)
        symlink = self.root / (self.name + "-sym"); symlink.symlink_to(self.sample)
        fifo = self.root / (self.name + "-fifo"); os.mkfifo(fifo)
        for path in ("absent", str(directory), str(symlink), str(fifo)):
            with self.subTest(path=path):
                self.assertFalse(self.api("inspect_target", path=path)["success"])
        self.sample.chmod(0o000)
        error = self.api("inspect_target", path=self.name)["error"]
        self.assertEqual(error["details"]["errno"], 13)
        self.sample.chmod(0o600)
        os.link(self.sample, self.root / (self.name + "-hard"))
        self.assertEqual(self.api("preview_shred", path=self.name)["error"]["code"], "UNSAFE_TARGET")

    def test_traversal_and_canary(self):
        canary = self.base / "outside"
        canary.write_text("must survive"); canary.chmod(0o600)
        for path in ("../outside", str(canary), "sub/../../outside", "/etc/passwd",
                     ".agent13-service.lock", self.name + "/../outside"):
            self.assertFalse(self.api("preview_shred", path=path)["success"])
        link = self.root / (self.name + "-parent")
        link.symlink_to(self.base, target_is_directory=True)
        self.assertFalse(self.api("preview_shred", path=link.name + "/outside")["success"])
        self.assertEqual(canary.read_text(), "must survive")

    def test_invalid_token_and_file_replacement(self):
        self.assertEqual(self.api("execute_shred", path=self.name, confirm_token="invalid")["error"]["code"], "INVALID_TOKEN")
        token = self.api("preview_shred", path=self.name)["data"]["confirm_token"]
        replacement = self.root / (self.name + "-replacement")
        replacement.write_bytes(b"replacement"); replacement.chmod(0o600)
        replacement.replace(self.sample)
        result = self.api("execute_shred", path=self.name, confirm_token=token)
        self.assertEqual(result["error"]["code"], "FILE_CHANGED")
        self.assertEqual(self.sample.read_bytes(), b"replacement")

    def test_json_and_framing_errors_service_survives(self):
        for payload in (b"{\n", b"[]\n", b"\xff\n", b'{"x":1,"x":2}\n',
                        b"{}", protocol.encode(request()) + b"{}\n",
                        b"x" * (protocol.MAX_REQUEST + 1)):
            with self.subTest(payload=payload[:30]):
                self.assertFalse(self.raw(payload)["success"])
        wrong = request(); wrong["agent_id"] = True
        self.assertFalse(self.raw(protocol.encode(wrong))["success"])
        self.assertTrue(self.api("health")["success"])

    def test_reject_second_instance_same_root(self):
        p = subprocess.run([sys.executable, "-m", "agent13.service", "--allowed-root",
                            str(self.root), "--socket", str(self.base / "other.sock")],
                           cwd=PROJECT, capture_output=True, timeout=5)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(self.api("health")["success"])


class LifecycleTests(unittest.TestCase):
    def test_shutdown_restart_and_token_invalidation(self):
        with tempfile.TemporaryDirectory(prefix="agent13-lifecycle-") as folder:
            base = Path(folder); root = base / "data"; root.mkdir(mode=0o700)
            sample = root / "sample"; sample.write_bytes(b"disposable"); sample.chmod(0o600)
            sock = base / "agent13.sock"
            process, log = start_service(root, sock)
            try:
                token = call(sock, "preview_shred", {"path": "sample"})["data"]["confirm_token"]
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertFalse(sock.exists())
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=5)
                log.close()
            process, log = start_service(root, sock)
            try:
                result = call(sock, "execute_shred", {"path": "sample", "confirm_token": token})
                self.assertEqual(result["error"]["code"], "INVALID_TOKEN")
                self.assertTrue(sample.exists())
            finally:
                process.send_signal(signal.SIGINT); process.wait(timeout=5); log.close()
            self.assertEqual(process.returncode, 0)
            self.assertFalse(sock.exists())

    def test_no_service_cli_error(self):
        with tempfile.TemporaryDirectory(prefix="agent13-client-") as folder:
            p = subprocess.run([str(PROJECT / "bin/agent13ctl"), "--socket", folder + "/missing.sock", "health"],
                               capture_output=True, text=True, timeout=5)
            self.assertEqual(p.returncode, 2)
            self.assertEqual(json.loads(p.stdout)["error"]["code"], "CLIENT_ERROR")
