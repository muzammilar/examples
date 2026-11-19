"""Negative controls for fencing, survivor proof and reported latency boundaries."""

import unittest
from unittest.mock import patch
import control


class Controls(unittest.TestCase):
    def test_empty_device_proof_fails_on_failed_listing_and_nonempty_directory(self):
        import subprocess, tempfile, shlex
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path, expected in [(root / "missing", False), (root, True)]:
                script = control.EMPTY_VOLUME_CHECK.replace(
                    "/var/lib/postgresql/patroni", shlex.quote(str(path))
                )
                result = subprocess.run(
                    ["sh", "-ec", script], capture_output=True, text=True
                )
                self.assertEqual(result.returncode == 0, expected)
            (root / "unexpected").touch()
            result = subprocess.run(
                [
                    "sh",
                    "-ec",
                    control.EMPTY_VOLUME_CHECK.replace(
                        "/var/lib/postgresql/patroni", shlex.quote(str(root))
                    ),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("EMPTY_OWNED_VOLUME", result.stdout)

    def test_replacement_does_not_attach_a_foreign_volume(self):
        import json

        target = "pg2"
        volume = {
            "Name": "owned",
            "CreatedAt": "old",
            "Labels": {
                "com.docker.compose.project": control.PROJECT,
                "com.docker.compose.volume": target,
            },
        }
        foreign = {
            "CreatedAt": "new",
            "Labels": {"com.docker.compose.project": "foreign"},
        }
        with patch.object(
            control, "call", side_effect=["", "owned", json.dumps([foreign])]
        ) as runtime:
            with self.assertRaises(RuntimeError):
                control.provision_replacement(target, "old-id", volume, control.RESULTS)
            self.assertFalse(
                any("create" in call.args[0] for call in runtime.call_args_list)
            )

    def test_replacement_does_not_erase_an_original_volume(self):
        import json

        target = "pg2"
        volume = {
            "Name": "owned",
            "CreatedAt": "old",
            "Labels": {
                "com.docker.compose.project": control.PROJECT,
                "com.docker.compose.volume": target,
            },
        }
        with patch.object(
            control, "call", side_effect=["", "owned", json.dumps([volume])]
        ) as runtime:
            with self.assertRaises(RuntimeError):
                control.provision_replacement(target, "old-id", volume, control.RESULTS)
            self.assertFalse(
                any(
                    "create" in call.args[0] or "rm" in call.args[0]
                    for call in runtime.call_args_list
                )
            )

    def test_mount_order_is_not_a_storage_identity_change(self):
        first = {
            "Type": "volume",
            "Destination": "/data",
            "Name": "owned",
            "Source": "/volumes/owned",
            "RW": True,
        }
        second = {
            "Type": "volume",
            "Destination": "/other",
            "Name": "another",
            "Source": "/volumes/another",
            "RW": True,
        }
        self.assertEqual(
            control.mount_records({"Mounts": [first, second]}),
            control.mount_records({"Mounts": [second, first]}),
        )
        changed = first | {"Name": "replacement"}
        self.assertNotEqual(
            control.mount_records({"Mounts": [first, second]}),
            control.mount_records({"Mounts": [changed, second]}),
        )
        with self.assertRaises(RuntimeError):
            control.mount_records({"Mounts": [first, first]})

    def test_stale_command_result_cannot_be_reused(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "command-state.json"
            previous.write_text('{"old":true}')
            with (
                patch.object(control, "RESULTS", root),
                patch.object(
                    control, "call", return_value="command exited without output"
                ),
            ):
                with self.assertRaises(RuntimeError):
                    control.tools("state")
            self.assertFalse(previous.exists())
            self.assertEqual(len(list((root / "command-history").glob("*.json"))), 1)

    def test_unknown_case_cannot_report_a_false_pass(self):
        with patch.object(control, "containers") as native:
            with self.assertRaises(ValueError):
                control.experiment("typo-in-disk-failure")
            native.assert_not_called()

    def test_failed_inspection_is_unknown_not_fenced(self):
        with patch.object(
            control, "inspect_id", side_effect=RuntimeError("daemon unavailable")
        ):
            with self.assertRaises(RuntimeError):
                control.fenced("old-id")

    def test_fencing_requires_actual_sigkill(self):
        for state in [
            dict(Running=True, ExitCode=137, OOMKilled=False),
            dict(Running=False, ExitCode=0, OOMKilled=False),
            dict(Running=False, ExitCode=137, OOMKilled=True),
        ]:
            with patch.object(control, "inspect_id", return_value={"State": state}):
                with self.assertRaises(RuntimeError):
                    control.fenced("old-id")

    def test_wrong_project_or_identity_is_rejected(self):
        for identity, project in [
            ("wrong", "pgvector-product-search"),
            ("wanted", "foreign"),
        ]:
            import json

            row = {
                "Id": identity,
                "Config": {"Labels": {"com.docker.compose.project": project}},
            }
            with patch.object(control, "call", return_value=json.dumps([row])):
                with self.assertRaises(RuntimeError):
                    control.inspect_id("wanted")

    def test_fast_failures_do_not_improve_success_latency(self):
        events = [
            dict(time=1, stage="ack", rows=[{}] * 10, seconds=1),
            dict(time=2, stage="ambiguous", rows=[{}] * 10, seconds=0.001),
        ]
        result = control.window(events, 0, 3)
        self.assertEqual(result["ack"]["p99_ms"], 1000)
        self.assertEqual(result["ambiguous"]["p99_ms"], 1)
        self.assertEqual(result["ack"]["rows"], 10)
        self.assertEqual(result["ack"]["latency_samples"], 1)

    def test_phase_windows_are_half_open(self):
        events = [
            dict(time=1, stage="get", seconds=0.01),
            dict(time=2, stage="get_error", seconds=0.001),
        ]
        self.assertEqual(set(control.window(events, 1, 2)), {"get"})

    def test_no_progress_when_actor_exited(self):
        from unittest.mock import Mock

        actor = Mock()
        actor.poll.return_value = 1
        with self.assertRaises(RuntimeError):
            control.prove_progress(control.RESULTS, 0, {"writer": actor})


if __name__ == "__main__":
    unittest.main()
