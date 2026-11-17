"""Failure evidence must survive timeout, and unknown Docker state is not fencing."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import experiment


class FailureEvidence(unittest.TestCase):
    def run_failure(self, known):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "runs" / "failure-evidence"
            run.mkdir(parents=True)
            plans = root / "plans"
            plans.mkdir()
            (plans / "rayforce-failure-evidence.tsv").write_text("retained plan")
            state = {
                "Config": {
                    "Labels": {"com.docker.compose.project": experiment.PROJECT}
                },
                "State": {"Running": True},
            }
            captures = ([json.dumps([state])] if known else []) + [
                subprocess.TimeoutExpired(
                    "docker stats" if known else "docker inspect", 30
                ),
                "partial native output\n",
            ]
            with (
                patch.object(experiment, "RESULTS", root),
                patch.object(experiment, "command") as command,
                patch.object(experiment, "capture", side_effect=captures),
            ):
                with self.assertRaises(subprocess.TimeoutExpired):
                    experiment.run_case("cpp", "rayforce", 1, run, 2, 1)
            failure = json.loads(next(run.glob("*-failure.json")).read_text())
            self.assertIn("timed out", failure["error"])
            self.assertEqual(
                next(run.glob("*.log")).read_text(), "partial native output\n"
            )
            self.assertEqual(failure["last_inspection"], state if known else None)
            removals = [
                call.args[0]
                for call in command.call_args_list
                if call.args[0][:2] == ["docker", "rm"]
            ]
            self.assertEqual(len(removals), 1 if known else 0)
            self.assertEqual(list(run.glob("*-result.json")), [])

    def test_stats_timeout_retains_partial_evidence(self):
        self.run_failure(known=True)

    def test_unknown_runtime_state_never_counts_as_fenced(self):
        self.run_failure(known=False)


if __name__ == "__main__":
    unittest.main()
