import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from curate import validate_phase


class EvidenceGateTests(unittest.TestCase):
    def test_recovery_preserves_failed_original_status(self):
        with tempfile.TemporaryDirectory() as folder:
            repair = Path(folder)
            (repair / "automatic-repair.json").write_text(
                json.dumps({"failed_case": "datanode-failure-1"})
            )
            status = {"passed": False}
            validate_phase(
                "datanode-failure",
                Path("datanode-failure-1"),
                status,
                {"observe-repair": (repair, {"passed": True})},
            )
            self.assertFalse(status["passed"])

    def test_other_case_recovery_cannot_hide_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            repair = Path(folder)
            (repair / "automatic-repair.json").write_text(
                json.dumps({"failed_case": "datanode-failure-older"})
            )
            with self.assertRaises(ValueError):
                validate_phase(
                    "datanode-failure",
                    Path("datanode-failure-newer"),
                    {"passed": False},
                    {"observe-repair": (repair, {"passed": True})},
                )

    def test_latest_failed_baseline_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_phase("test", Path("test-1"), {"passed": False}, {})
