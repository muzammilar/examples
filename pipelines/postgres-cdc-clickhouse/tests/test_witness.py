"""Witnesses survive runs but must never leak between independent fixtures."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
correctness = importlib.import_module("correctness")


class FixtureWitness(unittest.TestCase):
    def test_previous_fixture_is_not_current_acknowledged_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "results/cases/previous"
            case.mkdir(parents=True)
            (case / "case.json").write_text(json.dumps({"fixture": "previous"}))
            (case / "witness.jsonl").write_text("old history must not be interpreted\n")
            bodies = [{"event": "verified", "body_sha256": "same"}] * 3
            with patch.object(correctness, "ROOT", root), patch.object(
                correctness, "fixture", return_value={"id": "current"}
            ), patch.object(correctness, "app", return_value=bodies), patch.object(
                correctness, "pg", return_value="[]"
            ):
                with self.assertRaisesRegex(RuntimeError, "No external source witness"):
                    correctness.reconcile(root / "report")


if __name__ == "__main__":
    unittest.main()
