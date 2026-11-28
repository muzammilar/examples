"""Reject false-success REST replies and foreign container ownership."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import types
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/lab.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("handoff_control", SCRIPT)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ControlGuards(unittest.TestCase):
    def test_json_error_http_status_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "409"):
            control.decode_connect_reply('{"error_code":409}\n409')

    def test_bad_success_body_is_rejected(self):
        with self.assertRaises(json.JSONDecodeError):
            control.decode_connect_reply("not-json\n200")

    def test_empty_delete_success(self):
        self.assertIsNone(control.decode_connect_reply("\n204"))

    def test_foreign_project_is_not_an_owned_target(self):
        row = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "another-project",
                    "com.docker.compose.service": "connect",
                }
            }
        }
        with patch.object(
            control, "command", return_value=types.SimpleNamespace(stdout="abc123\n")
        ), patch.object(
            control.subprocess, "check_output", return_value=json.dumps([row])
        ):
            with self.assertRaisesRegex(RuntimeError, "Foreign"):
                control.owned("connect")


if __name__ == "__main__":
    unittest.main()
