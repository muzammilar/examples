"""Controller safety boundaries; native S3/Spark execution is a separate gate."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import compactor_control as compactor


class ControllerSafety(unittest.TestCase):
    def test_reporting_error_does_not_replace_interruption(self):
        primary = KeyboardInterrupt("controller interrupted")
        with patch.object(
            compactor.control, "save", side_effect=OSError("witness disk unavailable")
        ):
            compactor.record_failure(Path("/unused"), primary)
        self.assertIn("witness disk unavailable", " ".join(primary.__notes__))

    def test_auxiliary_source_manifest_must_match_the_frozen_namespace(self):
        expected = {"iceberg-compaction/compact.py": "a" * 64}
        value = {"Id": "owned"}
        metadata = {"source_sha256": expected}
        with patch.object(
            compactor.control, "call", return_value="a" * 64 + "  compact.py\n"
        ):
            self.assertEqual(
                compactor.helper_sources(value, "recovery-compactor", metadata),
                {"sources": expected},
            )
        for body in (
            "b" * 64 + "  compact.py\n",
            "a" * 64 + "  ../compact.py\n",
            "a" * 64 + "  compact.py\n" + "a" * 64 + "  compact.py\n",
        ):
            with (
                self.subTest(body=body),
                patch.object(compactor.control, "call", return_value=body),
            ):
                with self.assertRaises(RuntimeError):
                    compactor.helper_sources(value, "recovery-compactor", metadata)

    def test_failed_inspection_is_unknown_not_proof_to_stop_or_replace(self):
        with (
            patch.object(
                compactor.control,
                "inspect_client",
                side_effect=RuntimeError("daemon unavailable"),
            ),
            patch.object(compactor.control, "call") as mutate,
        ):
            with self.assertRaisesRegex(RuntimeError, "daemon unavailable"):
                compactor.end_helper(
                    SimpleNamespace(role="fault-compactor"), None, stop=True
                )
            mutate.assert_not_called()

    def test_gate_journal_cannot_escape_its_owned_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary).resolve()
            client = SimpleNamespace(directory=case / "maintenance" / "invocation")
            for path in ("../other", "/etc/passwd", "upload/../../../other"):
                with self.subTest(path=path), self.assertRaises(RuntimeError):
                    compactor.gate_file(case, client, path)

    def test_spark_report_requires_exactly_one_completed_invocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary).resolve()
            client = SimpleNamespace(directory=case / "maintenance" / "invocation")
            output = client.directory / "spark"
            output.mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                compactor.spark_report(case, client)
            file = output / "compaction-first-resources.json"
            file.write_text(json.dumps({"passed": False, "stop": {"passed": True}}))
            with self.assertRaises(RuntimeError):
                compactor.spark_report(case, client)
            file.write_text(json.dumps({"passed": True, "stop": {"passed": True}}))
            self.assertEqual(compactor.spark_report(case, client), file)
            (output / "compaction-second-resources.json").write_text(file.read_text())
            with self.assertRaises(RuntimeError):
                compactor.spark_report(case, client)

    def test_survivor_restarts_are_not_hidden_by_current_readiness(self):
        original = {
            "Id": "storage",
            "State": {"Running": True, "Pid": 1, "StartedAt": "original"},
            "RestartCount": 0,
        }
        current = original | {
            "State": original["State"] | {"Pid": 2, "StartedAt": "restarted"}
        }
        with (
            patch.object(compactor.control, "check_actors"),
            patch.object(compactor.control, "owned", return_value=current),
        ):
            with self.assertRaisesRegex(RuntimeError, "survivor restarted"):
                compactor.survivors(original, original, {})


if __name__ == "__main__":
    unittest.main()
