import fcntl
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from agent13.client import request
from agent13.native import NativeError
from agent13.service import Agent, RequestError, handle_connection

PROJECT = Path(__file__).resolve().parent.parent


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="a13-unit-")
        self.root = Path(self.temp.name)
        self.agent = Agent(self.root, max_bytes=131072, ttl=120)
        self.uid = os.geteuid()

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def sample(self, name="sample", content=b"lab test data\n"):
        path = self.root / name
        path.write_bytes(content)
        path.chmod(0o600)
        return path

    def plan(self, path="sample", **options):
        return self.agent.dispatch("agent13_plan", {"path": path, **options}, self.uid)

    def execute(self, token, **options):
        return self.agent.dispatch("agent13_execute", {"token": token, "acknowledge_limitations": True, **options}, self.uid)

    def test_preview_does_not_change_contents(self):
        file = self.sample()
        before = file.stat()
        result = self.plan()
        self.assertEqual(file.read_bytes(), b"lab test data\n")
        self.assertEqual(file.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(result["file"]["inode"], before.st_ino)
        self.assertIn("not guaranteed", result["limitation"])

    def test_random_overwrite_of_multiple_chunks(self):
        content = b"Q" * 131071
        file = self.sample(content=content)
        with file.open("rb") as observer:
            result = self.execute(self.plan(passes=2)["token"])
            overwritten = observer.read()
        self.assertFalse(file.exists())
        self.assertEqual(len(overwritten), len(content))
        self.assertNotEqual(overwritten, content)
        self.assertNotEqual(overwritten, bytes(len(content)))
        self.assertEqual(result["file"]["bytes_written"], len(content) * 2)
        self.assertFalse(result["physical_erasure_guaranteed"])

    def test_zero_pass_and_unlink(self):
        file = self.sample(content=b"secret" * 1000)
        with file.open("rb") as observer:
            result = self.execute(self.plan(passes=1, zero_pass=True)["token"])
            self.assertEqual(observer.read(), bytes(6000))
        self.assertFalse(file.exists())
        self.assertTrue(result["file"]["directory_synced"])
        self.assertEqual(result["file"]["bytes_written"], 12000)

    def test_empty_file(self):
        file = self.sample(content=b"")
        self.assertEqual(self.execute(self.plan()["token"])["file"]["bytes_written"], 0)
        self.assertFalse(file.exists())

    def test_unicode_spaces_and_shell_characters_are_literal(self):
        name = "tax ₹ return;$(touch SHOULD_NOT_EXIST)"
        file = self.sample(name)
        self.execute(self.plan(name, passes=1)["token"])
        self.assertFalse(file.exists())
        self.assertFalse((PROJECT / "SHOULD_NOT_EXIST").exists())

    def test_cancel_releases_lock(self):
        file = self.sample()
        token = self.plan()["token"]
        self.agent.dispatch("agent13_cancel", {"token": token}, self.uid)
        self.assertEqual(file.read_bytes(), b"lab test data\n")
        self.plan()
        with self.assertRaises(RequestError):
            self.execute(token)

    def test_acknowledgement_required_without_consuming_plan(self):
        self.sample()
        token = self.plan()["token"]
        for wrong in (False, "true", 1, None):
            with self.subTest(wrong=wrong), self.assertRaises(RequestError):
                self.execute(token, acknowledge_limitations=wrong)
        self.execute(token)

    def test_execute_token_cannot_be_replayed(self):
        self.sample()
        token = self.plan()["token"]
        self.execute(token)
        with self.assertRaises(RequestError):
            self.execute(token)

    def test_expiry_releases_handle(self):
        self.sample()
        token = self.plan()["token"]
        self.agent.plans[token]["deadline"] = time.monotonic() - 1
        with self.assertRaises(RequestError):
            self.execute(token)
        self.plan()

    def test_token_bound_to_uid(self):
        file = self.sample()
        token = self.plan()["token"]
        with self.assertRaises(RequestError):
            self.agent.dispatch("agent13_cancel", {"token": token}, self.uid + 1)
        self.assertTrue(file.exists())

    def test_file_changed_after_plan_refused_and_token_consumed(self):
        file = self.sample()
        token = self.plan()["token"]
        file.write_bytes(b"changed")
        with self.assertRaises(NativeError) as caught:
            self.execute(token)
        self.assertEqual(caught.exception.details["bytes_written"], 0)
        self.assertEqual(file.read_bytes(), b"changed")
        with self.assertRaises(RequestError):
            self.execute(token)

    def test_same_size_change_after_plan_refused(self):
        file = self.sample(content=b"AAAA")
        token = self.plan()["token"]
        file.write_bytes(b"BBBB")
        with self.assertRaises(NativeError):
            self.execute(token)
        self.assertEqual(file.read_bytes(), b"BBBB")

    def test_replaced_inode_refused(self):
        file = self.sample()
        token = self.plan()["token"]
        file.rename(self.root / "original")
        self.sample(content=b"replacement")
        with self.assertRaises(NativeError):
            self.execute(token)
        self.assertEqual(file.read_bytes(), b"replacement")
        self.assertEqual((self.root / "original").read_bytes(), b"lab test data\n")

    def test_renamed_parent_refused(self):
        (self.root / "nested").mkdir(mode=0o700)
        self.sample("nested/sample")
        token = self.plan("nested/sample")["token"]
        (self.root / "nested").rename(self.root / "moved")
        with self.assertRaises(NativeError):
            self.execute(token)
        self.assertEqual((self.root / "moved/sample").read_bytes(), b"lab test data\n")

    def test_nested_directory_supported(self):
        (self.root / "nested").mkdir(mode=0o700)
        file = self.sample("nested/sample")
        self.execute(self.plan("nested/sample")["token"])
        self.assertFalse(file.exists())

    def test_traversal_and_invalid_paths(self):
        self.sample()
        for path in ("/etc/passwd", "../sample", "a/../sample", "./sample", "sample/", "a//sample", "", "\x00sample"):
            with self.subTest(path=path), self.assertRaises((RequestError, NativeError)):
                self.plan(path)
        self.assertEqual((self.root / "sample").read_bytes(), b"lab test data\n")

    def test_symlink_file_refused(self):
        file = self.sample()
        (self.root / "link").symlink_to(file)
        with self.assertRaises(NativeError):
            self.plan("link")
        self.assertEqual(file.read_bytes(), b"lab test data\n")

    def test_symlink_directory_refused(self):
        (self.root / "dir").mkdir(mode=0o700)
        file = self.sample("dir/sample")
        (self.root / "alias").symlink_to("dir", target_is_directory=True)
        with self.assertRaises(NativeError):
            self.plan("alias/sample")
        self.assertTrue(file.exists())

    def test_hard_links_refused_before_and_after_plan(self):
        file = self.sample()
        os.link(file, self.root / "alias")
        with self.assertRaises(NativeError):
            self.plan()
        (self.root / "alias").unlink()
        token = self.plan()["token"]
        os.link(file, self.root / "alias")
        with self.assertRaises(NativeError):
            self.execute(token)
        self.assertEqual(file.read_bytes(), b"lab test data\n")

    def test_fifo_and_directory_refused(self):
        (self.root / "dir").mkdir()
        os.mkfifo(self.root / "fifo", mode=0o600)
        for path in ("dir", "fifo"):
            with self.subTest(path=path), self.assertRaises(NativeError):
                self.plan(path)

    def test_file_permissions_refused(self):
        file = self.sample()
        for mode in (0o400, 0o620, 0o602):
            file.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(NativeError):
                self.plan()
        file.chmod(0o600)

    def test_directory_and_root_permissions_revalidated(self):
        (self.root / "nested").mkdir(mode=0o700)
        file = self.sample("nested/sample")
        (self.root / "nested").chmod(0o777)
        with self.assertRaises(NativeError):
            self.plan("nested/sample")
        (self.root / "nested").chmod(0o700)
        token = self.plan("nested/sample")["token"]
        self.root.chmod(0o755)
        with self.assertRaises(NativeError):
            self.execute(token)
        self.root.chmod(0o700)
        self.assertTrue(file.exists())

    def test_unwritable_parent_refused_before_overwriting(self):
        (self.root / "nested").mkdir(mode=0o700)
        file = self.sample("nested/sample")
        (self.root / "nested").chmod(0o500)
        try:
            with self.assertRaises(NativeError):
                self.plan("nested/sample")
            self.assertEqual(file.read_bytes(), b"lab test data\n")
        finally:
            (self.root / "nested").chmod(0o700)

    def test_sparse_file_and_size_limit_refused(self):
        file = self.sample(content=b"A")
        with file.open("r+b") as stream:
            stream.truncate(65536)
        with self.assertRaises(NativeError):
            self.plan()
        file.write_bytes(b"A" * 131073)
        with self.assertRaises(NativeError):
            self.plan()

    def test_conflicting_lock_refused(self):
        file = self.sample()
        with file.open("r+b") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(NativeError):
                self.plan()
        self.plan()
        with self.assertRaises(NativeError):
            self.plan()

    def test_plan_cap_and_unknown_arguments(self):
        self.agent.max_plans = 1
        self.sample()
        self.plan()
        self.sample("second")
        with self.assertRaises(RequestError):
            self.plan("second")
        for tool, args in (("agent13_health", {"extra": True}), ("unknown", {}),
                           ([], {}), ("agent13_health", [])):
            with self.subTest(tool=tool), self.assertRaises(RequestError):
                self.agent.dispatch(tool, args, self.uid)

    def test_invalid_options(self):
        self.sample()
        for args in ({"passes": 0}, {"passes": 8}, {"passes": True}, {"passes": "3"},
                     {"zero_pass": 1}, {"extra": 1}):
            with self.subTest(args=args), self.assertRaises(RequestError):
                self.plan(**args)

    def test_preparation_and_expiry_do_not_leak_fds(self):
        self.sample()
        initial = len(os.listdir("/proc/self/fd"))
        for _ in range(40):
            token = self.plan()["token"]
            self.agent.dispatch("agent13_cancel", {"token": token}, self.uid)
        self.assertEqual(len(os.listdir("/proc/self/fd")), initial)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="a13-sock-")
        self.directory = Path(self.temp.name)
        self.root = self.directory / "vault"
        self.root.mkdir(mode=0o700)
        self.socket = str(self.directory / "runtime/control.sock")
        self.log = (self.directory / "service.log").open("w+")
        self.process = subprocess.Popen([sys.executable, str(PROJECT / "run_service.py"),
                                         "--root", str(self.root), "--socket", self.socket,
                                         "--plan-ttl", "5"], cwd=PROJECT, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                self.call("health")
                return
            except OSError:
                pass
            if self.process.poll() is not None:
                break
            time.sleep(0.02)
        self.log.seek(0)
        diagnostic = self.log.read()
        self.tearDown()
        self.fail("service did not start: " + diagnostic)

    def tearDown(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()
        self.temp.cleanup()

    def call(self, name, args=None):
        return request("agent13_" + name, args, self.socket, 5)

    def sample(self):
        file = self.root / "sample"
        file.write_bytes(b"DISPOSABLE LAB FIXTURE")
        file.chmod(0o600)
        return file

    def raw(self, data):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(4)
            sock.connect(self.socket)
            sock.sendall(data)
            received = bytearray()
            while b"\n" not in received:
                received.extend(sock.recv(4096))
        return json.loads(received.partition(b"\n")[0])

    def test_background_pid_survives_requests(self):
        first, second = self.call("health"), self.call("health")
        self.assertTrue(first["ok"])
        self.assertEqual(first["result"]["pid"], self.process.pid)
        self.assertEqual(first["result"]["pid"], second["result"]["pid"])
        self.assertEqual(Path(self.socket).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.socket).parent.stat().st_mode & 0o777, 0o700)

    def test_socket_plan_execute_replay(self):
        file = self.sample()
        plan = self.call("plan", {"path": "sample", "passes": 1})
        self.assertTrue(plan["ok"], plan)
        token = plan["result"]["token"]
        self.assertTrue(file.exists())
        result = self.call("execute", {"token": token, "acknowledge_limitations": True})
        self.assertTrue(result["ok"], result)
        self.assertFalse(file.exists())
        self.assertEqual(self.call("execute", {"token": token, "acknowledge_limitations": True})["error"]["code"], "invalid_token")

    def test_socket_cancel_and_automatic_expiry(self):
        file = self.sample()
        token = self.call("plan", {"path": "sample"})["result"]["token"]
        self.assertTrue(self.call("cancel", {"token": token})["ok"])
        token = self.call("plan", {"path": "sample"})["result"]["token"]
        time.sleep(5.2)
        self.assertEqual(self.call("execute", {"token": token, "acknowledge_limitations": True})["error"]["code"], "invalid_token")
        self.assertEqual(file.read_bytes(), b"DISPOSABLE LAB FIXTURE")

    def test_malformed_requests_do_not_crash_service(self):
        cases = [b"not json\n", b"[]\n", b'{"id":"a","id":"b"}\n', b"\xff\n",
                 b'{"version":true,"id":"a","tool":"agent13_health","arguments":{}}\n',
                 b'{"version":1,"id":"a","tool":[],"arguments":{}}\n',
                 b'{"version":1,"id":"a","tool":"agent13_plan","arguments":{"path":"\\ud800"}}\n',
                 b'{"version":1,"id":"a","tool":"agent13_health","arguments":{"a":NaN}}\n',
                 b"[" * 1100 + b"\n", b"x" * 16385 + b"\n"]
        for case in cases:
            with self.subTest(case=case[:80]):
                self.assertFalse(self.raw(case)["ok"])
                self.assertTrue(self.call("health")["ok"])

    def test_connection_timeout(self):
        self.assertEqual(self.raw(b"{")["error"]["code"], "invalid_request")
        self.assertTrue(self.call("health")["ok"])

    def test_single_instance_lock(self):
        other = subprocess.run([sys.executable, str(PROJECT / "run_service.py"), "--root", str(self.root),
                                "--socket", self.socket], capture_output=True, timeout=5)
        self.assertEqual(other.returncode, 1)
        self.assertTrue(self.call("health")["ok"])

    def test_restart_after_crash_removes_stale_socket(self):
        self.process.kill()
        self.process.wait(timeout=5)
        self.assertTrue(Path(self.socket).exists())
        self.process = subprocess.Popen([sys.executable, str(PROJECT / "run_service.py"),
                                         "--root", str(self.root), "--socket", self.socket],
                                        cwd=PROJECT, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                result = self.call("health")
                self.assertEqual(result["result"]["pid"], self.process.pid)
                return
            except OSError:
                time.sleep(0.02)
        self.fail("daemon failed to recover stale socket")

    def test_staged_installation(self):
        stage = self.directory / "stage"
        installed = subprocess.run(["make", "install", "DESTDIR=" + str(stage), "PREFIX=/usr"],
                                   cwd=PROJECT, capture_output=True, text=True, timeout=10)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn("/usr/lib/agent13/run_service.py", (stage / "usr/lib/systemd/user/agent13.service").read_text())
        result = subprocess.run([sys.executable, str(stage / "usr/bin/agent13"), "--socket", self.socket, "health"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.process.terminate()
        self.process.wait(timeout=5)
        self.process = subprocess.Popen([sys.executable, str(stage / "usr/lib/agent13/run_service.py"),
                                         "--root", str(self.root), "--socket", self.socket],
                                         stdout=self.log, stderr=self.log)
        for _ in range(100):
            if Path(self.socket).exists():
                self.assertTrue(self.call("health")["ok"])
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.02)
        self.fail("staged daemon failed to start")

    def test_shutdown_cleans_socket_and_keeps_pending_file(self):
        file = self.sample()
        self.call("plan", {"path": "sample"})
        self.process.terminate()
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertFalse(Path(self.socket).exists())
        self.assertEqual(file.read_bytes(), b"DISPOSABLE LAB FIXTURE")

    def test_cli_full_flow(self):
        file = self.sample()
        base = [sys.executable, str(PROJECT / "bin/agent13"), "--socket", self.socket]
        plan = subprocess.run(base + ["plan", "sample", "--passes", "1", "--zero-pass"], capture_output=True, text=True, timeout=5)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        token = json.loads(plan.stdout)["result"]["token"]
        missing_ack = subprocess.run(base + ["execute", token], capture_output=True, text=True, timeout=5)
        self.assertEqual(missing_ack.returncode, 2)
        execute = subprocess.run(base + ["execute", token, "--acknowledge-limitations"], capture_output=True, text=True, timeout=5)
        self.assertEqual(execute.returncode, 0, execute.stderr + execute.stdout)
        self.assertFalse(file.exists())

    def test_cli_server_and_connection_errors(self):
        base = [sys.executable, str(PROJECT / "bin/agent13"), "--socket", self.socket]
        result = subprocess.run(base + ["plan", "missing"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.process.terminate()
        self.process.wait(timeout=5)
        result = subprocess.run(base + ["health"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "connection_error")

    def test_master_adapter_requires_exact_human_approved_token(self):
        spec = importlib.util.spec_from_file_location("adapter", PROJECT / "examples/class_shell_adapter.py")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        self.sample()
        plan = adapter.dispatch_tool("agent13_plan", {"path": "sample"}, socket_path=self.socket)
        token = plan["result"]["token"]
        args = {"token": token, "acknowledge_limitations": True}
        for approved in (None, "another-token"):
            self.assertEqual(adapter.dispatch_tool("agent13_execute", args, socket_path=self.socket,
                             approved_token=approved)["error"]["code"], "human_approval_required")
        self.assertTrue(adapter.dispatch_tool("agent13_execute", args, socket_path=self.socket, approved_token=token)["ok"])

    def test_disposable_demo(self):
        result = subprocess.run([sys.executable, str(PROJECT / "examples/demo.py")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("The same service process remains alive", result.stdout)
        self.assertIn("temporary files and service cleaned up", result.stdout)


class PeerTests(unittest.TestCase):
    def test_peer_with_other_uid_refused_before_reading_request(self):
        import struct

        class ForeignConnection:
            reply = None

            def getsockopt(self, *_):
                return struct.pack("3i", 100, os.geteuid() + 1, 100)

            def sendall(self, data):
                self.reply = json.loads(data)

        connection = ForeignConnection()
        handle_connection(connection, None)
        self.assertEqual(connection.reply["error"]["code"], "unauthorized")


if __name__ == "__main__":
    unittest.main()
