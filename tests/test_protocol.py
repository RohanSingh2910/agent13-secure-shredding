import json
import unittest

from agent13 import protocol
from agent13.client import validate_response


def request(action="health", **params):
    return {"version": "1.0", "agent_id": 13, "request_id": "test-001",
            "action": action, "parameters": params}


class ProtocolTests(unittest.TestCase):
    def test_round_trip_and_manifest(self):
        value = request()
        self.assertEqual(protocol.validate(protocol.decode(protocol.encode(value))), value)
        with open("manifests/agent13.tools.json") as stream:
            manifest = json.load(stream)
        self.assertEqual({t["action"] for t in manifest["tools"]},
                         {"health", "inspect_target", "preview_shred", "execute_shred"})
        for tool in manifest["tools"]:
            self.assertEqual(set(tool["parameters"]["properties"]), protocol.ACTIONS[tool["action"]])

    def test_invalid_json(self):
        for value in (b"{", b"\xff", b'{"a":1,"a":2}', b'{"a":NaN}',
                      b'{"a":Infinity}', b"[" * 2000 + b"]" * 2000):
            with self.subTest(value=value[:50]), self.assertRaises(protocol.RequestError):
                protocol.decode(value)

    def test_request_validation(self):
        bad = [[], None, {}, request(action="unknown"), request(path="extra")]
        for key, value in (("version", "2.0"), ("agent_id", True), ("agent_id", 14),
                           ("action", []), ("parameters", []), ("request_id", "\ninjection"),
                           ("request_id", "a" * 129)):
            item = request(); item[key] = value; bad.append(item)
        bad.extend([request("inspect_target", path=1), request("execute_shred", path="a"),
                    request("execute_shred", path="a", confirm_token="")])
        extra = request(); extra["extra"] = 1; bad.append(extra)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(protocol.RequestError):
                protocol.validate(value)

    def test_response_validation(self):
        valid = protocol.response("test", data={"status": "ok"})
        self.assertEqual(validate_response(valid, "test"), valid)
        bad = [None, {}, {**valid, "request_id": "other"}, {**valid, "success": 1},
               {**valid, "error": {}}, {**valid, "data": []},
               {**valid, "success": False, "data": None, "error": {}}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_response(value, "test")
