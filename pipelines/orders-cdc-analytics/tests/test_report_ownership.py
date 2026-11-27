"""A newer foreign or failed run must never qualify this fixture's report."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import curate


class ReportOwnership(unittest.TestCase):
    def test_foreign_success_cannot_replace_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "results/benchmark-1"
            case.mkdir(parents=True)
            (case / "status.json").write_text(json.dumps({"passed": True}))
            (case / "fixture.json").write_text(json.dumps({"id": "foreign"}))
            with patch.object(curate.lab, "ROOT", root), patch.object(
                curate.lab, "fixture", return_value={"id": "owned"}
            ):
                with self.assertRaises(RuntimeError):
                    curate.latest("benchmark")

    def test_latest_owned_failure_is_not_hidden_by_older_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for sequence, passed in ((1, True), (2, False)):
                case = root / ("results/benchmark-" + str(sequence))
                case.mkdir(parents=True)
                (case / "status.json").write_text(json.dumps({"passed": passed}))
                (case / "fixture.json").write_text(json.dumps({"id": "owned"}))
            with patch.object(curate.lab, "ROOT", root), patch.object(
                curate.lab, "fixture", return_value={"id": "owned"}
            ):
                with self.assertRaises(RuntimeError):
                    curate.latest("benchmark")


if __name__ == "__main__":
    unittest.main()
