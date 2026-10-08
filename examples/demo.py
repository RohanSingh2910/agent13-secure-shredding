#!/usr/bin/python3
"""Runs a disposable lab demonstration, never reads or deletes user files."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from agent13.client import request


def main():
    with tempfile.TemporaryDirectory(prefix="a13-demo-") as temporary:
        directory = Path(temporary)
        root = directory / "vault"
        root.mkdir(mode=0o700)
        fixture = root / "lab-example.txt"
        fixture.write_bytes(b"DISPOSABLE OS LAB EXAMPLE\n")
        fixture.chmod(0o600)
        socket_path = str(directory / "runtime/control.sock")
        with (directory / "service.log").open("w+") as log:
            process = subprocess.Popen([sys.executable, str(PROJECT / "run_service.py"),
                                        "--root", str(root), "--socket", socket_path], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        health = request("agent13_health", socket_path=socket_path)
                        break
                    except OSError:
                        pass
                    time.sleep(0.02)
                else:
                    raise RuntimeError("service did not become ready within two seconds")
                health = request("agent13_health", socket_path=socket_path)
                print("1. Background service PID:", health["result"]["pid"])
                plan = request("agent13_plan", {"path": fixture.name, "passes": 1, "zero_pass": True}, socket_path)
                if not plan["ok"]:
                    raise RuntimeError(plan)
                print("2. Preview (fixture remains intact):")
                print(json.dumps(plan, indent=2))
                assert fixture.read_bytes() == b"DISPOSABLE OS LAB EXAMPLE\n"
                # This script authorizes only the synthetic file it just created.
                with fixture.open("rb") as observer:
                    result = request("agent13_execute", {"token": plan["result"]["token"],
                                     "acknowledge_limitations": True}, socket_path)
                    if not result["ok"]:
                        raise RuntimeError(result)
                    assert observer.read() == bytes(result["result"]["file"]["size"])
                print("3. Overwrite and unlink result:")
                print(json.dumps(result, indent=2))
                assert not fixture.exists()
                again = request("agent13_health", socket_path=socket_path)
                assert again["result"]["pid"] == process.pid
                print("4. The same service process remains alive.")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    print("Demo completed; temporary files and service cleaned up.")


if __name__ == "__main__":
    main()
