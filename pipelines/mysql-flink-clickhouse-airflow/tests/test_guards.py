"""Controller false-success and ownership controls; no live Docker required."""

import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
lab = importlib.import_module("lab")
correctness = importlib.import_module("correctness")


class Guards(unittest.TestCase):
    def test_seed_must_cover_the_historical_reader(self):
        from lifecycle import workload_counts

        with patch.dict(
            "os.environ", {"ORDER_COUNT": "100000", "CUSTOMER_COUNT": "10000"}
        ):
            with self.assertRaises(ValueError):
                workload_counts()

    def test_customer_mapping_must_preserve_immutable_reader_range(self):
        from lifecycle import workload_counts

        with patch.dict(
            "os.environ", {"ORDER_COUNT": "1000000", "CUSTOMER_COUNT": "1234"}
        ):
            with self.assertRaises(ValueError):
                workload_counts()

    def test_error_json_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "409"):
            lab.decode_http_reply('{"error_code":409}\n409')

    def test_broken_success_body_is_not_success(self):
        with self.assertRaises(json.JSONDecodeError):
            lab.decode_http_reply("broken\n200")

    def test_empty_delete_reply(self):
        self.assertIsNone(lab.decode_http_reply("\n204"))

    def test_foreign_role_cannot_be_a_fault_target(self):
        row = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "foreign",
                    "com.docker.compose.service": "connect",
                }
            }
        }
        with patch.object(
            lab, "command", return_value=types.SimpleNamespace(stdout="foreign-id\n")
        ), patch.object(lab.subprocess, "check_output", return_value=json.dumps([row])):
            with self.assertRaisesRegex(RuntimeError, "Foreign"):
                lab.owned("connect")

    def test_sql_error_with_exit_zero_is_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory, patch.object(
            lab,
            "command",
            return_value=types.SimpleNamespace(
                stdout="[ERROR] Could not execute SQL statement\nCGROUP cpu.stat\nCGROUP memory.peak\n",
                stderr="",
                returncode=0,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "exit zero"):
                lab.tool("sql-client", Path(directory))

    def test_an_already_fenced_source_is_not_revoked_again(self):
        def query(statement):
            if not statement.startswith("SELECT COUNT(*)"):
                self.fail("An already absent grant was revoked again")
            return "0"

        with patch.object(correctness, "mysql", side_effect=query):
            correctness.fence_writes()

    def test_partial_grants_are_configuration_drift(self):
        with patch.object(correctness, "mysql", return_value="2"):
            with self.assertRaisesRegex(RuntimeError, "partial source write grants"):
                correctness.fence_writes()


if __name__ == "__main__":
    unittest.main()
