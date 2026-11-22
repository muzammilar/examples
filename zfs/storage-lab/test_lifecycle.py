"""Reject cleanup without preserved native storage and verified ownership evidence."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run


class CleanupTests(unittest.TestCase):
    def test_stopped_storage_must_be_archived_before_deletion(self):
        with (
            patch.object(
                run, "inventory", return_value=[{"State": {"Running": False}}]
            ),
            patch.object(run.subprocess, "run") as execute,
        ):
            with self.assertRaisesRegex(RuntimeError, "start its guests"):
                run.execute(["docker-down"], Path("unused"))
            execute.assert_not_called()

    def test_unreachable_volume_owner_preserves_its_storage(self):
        with (
            patch.object(run, "inventory", return_value=[]),
            patch.object(run, "docker", return_value="owned-volume"),
            patch.object(run.subprocess, "run") as execute,
        ):
            with self.assertRaisesRegex(RuntimeError, "no reachable owner"):
                run.execute(["docker-down"], Path("unused"))
            execute.assert_not_called()

    def test_zero_teardown_exit_does_not_hide_remaining_resource(self):
        with tempfile.TemporaryDirectory() as directory:

            def docker(*args):
                if args[0] == "volume":
                    docker.calls += 1
                    return "" if docker.calls == 1 else "owned-volume"
                return ""

            docker.calls = 0
            with (
                patch.object(run, "inventory", return_value=[]),
                patch.object(run, "docker", side_effect=docker),
                patch.object(run.subprocess, "run") as execute,
            ):
                execute.return_value.returncode = 0
                with self.assertRaisesRegex(RuntimeError, "resources remain"):
                    run.execute(["docker-down"], Path(directory))
            self.assertTrue((Path(directory) / "teardown-verification.json").exists())


if __name__ == "__main__":
    unittest.main()
