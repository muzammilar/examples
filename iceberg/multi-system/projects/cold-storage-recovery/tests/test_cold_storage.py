"""Safety decisions for the cold whole-volume checkpoint; native restore is separate."""

import unittest
from unittest.mock import patch

import cold_storage as cold


class ColdStorageSafety(unittest.TestCase):
    def test_only_exact_local_owned_source_volume_is_accepted(self):
        volume = {
            "Name": "owned",
            "Driver": "local",
            "Labels": {"com.docker.compose.project": cold.control.PROJECT},
            "Options": None,
        }
        cold.validate_volume(volume, "owned")
        for changes in (
            {"Name": "other"},
            {"Driver": "nfs"},
            {"Labels": {}},
            {"Options": {"device": "/host"}},
        ):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                cold.validate_volume(volume | changes, "owned")

    def test_capabilities_are_limited_to_each_file_operation(self):
        self.assertEqual(cold.capabilities("backup"), ["DAC_OVERRIDE"])
        self.assertEqual(
            cold.capabilities("restore"), ["DAC_OVERRIDE", "CHOWN", "FOWNER"]
        )
        with self.assertRaises(ValueError):
            cold.capabilities("unknown")

    def test_nonzero_exit_is_not_a_clean_shutdown_or_a_fence(self):
        value = {
            "State": {
                "Running": False,
                "Pid": 0,
                "ExitCode": 0,
                "OOMKilled": False,
                "Error": "",
            }
        }
        cold.stopped(value)
        for changes in (
            {"Running": True},
            {"Pid": 7},
            {"ExitCode": 137},
            {"ExitCode": 143},
            {"OOMKilled": True},
            {"Error": "transport unknown"},
        ):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                cold.stopped({"State": value["State"] | changes})

    def services(self):
        return {
            role: {
                "Id": role,
                "Config": {},
                "State": {"Running": True, "Health": {"Status": "healthy"}},
            }
            for role in ("storage", "clickhouse")
        }

    def test_foreign_actor_in_project_blocks_before_any_stop(self):
        services = self.services()
        with (
            patch.object(cold.control, "service", side_effect=services.get),
            patch.object(
                cold.control, "call", return_value="storage\nclickhouse\nwriter\n"
            ) as call,
            self.assertRaises(RuntimeError),
        ):
            cold.original_services()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0][1], "ps")

    def test_native_pid_one_and_effective_signal_are_verified(self):
        for field, replacement in (
            ("signal", "SIGKILL"),
            ("health", "unhealthy"),
            ("pid", "entrypoint.sh"),
        ):
            with self.subTest(field=field):
                services = self.services()
                if field == "signal":
                    services["storage"]["Config"]["StopSignal"] = replacement
                if field == "health":
                    services["storage"]["State"]["Health"]["Status"] = replacement
                with (
                    patch.object(cold.control, "service", side_effect=services.get),
                    patch.object(
                        cold.control,
                        "call",
                        side_effect=["storage\nclickhouse\n", replacement],
                    ),
                    self.assertRaises(RuntimeError),
                ):
                    cold.original_services()

    def test_correct_actual_processes_pass_preflight(self):
        services = self.services()
        with (
            patch.object(cold.control, "service", side_effect=services.get),
            patch.object(
                cold.control,
                "call",
                side_effect=["storage\nclickhouse\n", "weed\n", "clickhouse-serv\n"],
            ),
        ):
            self.assertEqual(cold.original_services(), services)

    def test_runtime_failure_is_unknown_volume_ownership(self):
        with (
            patch.object(
                cold.control,
                "call",
                side_effect=RuntimeError("Docker transport failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            cold.volume_users("owned")


class CleanupSafety(unittest.TestCase):
    def test_inspection_failure_does_not_skip_sampler_shutdown(self):
        from unittest.mock import Mock
        from pathlib import Path

        sampler = Mock()
        sampler.close.return_value = ([], {})
        with (
            patch.object(
                cold, "inspect", side_effect=RuntimeError("transport unknown")
            ),
            patch.object(cold.control, "save"),
            patch.object(cold.control, "save_text"),
            patch.object(
                cold.subprocess, "run", return_value=Mock(returncode=0, stdout="")
            ),
        ):
            errors = cold.finish_helper("exact-id", Path("owned"), "backup", sampler)
        self.assertIn("transport unknown", str(errors[0]))
        sampler.close.assert_called_once()

    def test_live_pid_after_kill_is_not_successful_fencing(self):
        from unittest.mock import Mock
        from pathlib import Path

        sampler = Mock()
        sampler.close.return_value = ([], {})
        value = {
            "Id": "exact-id",
            "Image": "image",
            "RestartCount": 0,
            "State": {"Running": True, "Pid": 22},
        }
        with (
            patch.object(cold, "inspect", return_value=value),
            patch.object(cold.control, "call"),
            patch.object(cold.control, "save"),
            patch.object(cold.control, "save_text"),
            patch.object(
                cold.subprocess, "run", return_value=Mock(returncode=0, stdout="")
            ),
        ):
            errors = cold.finish_helper("exact-id", Path("owned"), "restore", sampler)
        self.assertIn("fencing is unproven", str(errors[0]))
        sampler.close.assert_called_once()

    def test_symlinked_results_and_lock_are_rejected(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            lab = root / "lab"
            lab.mkdir()
            (lab / "results").symlink_to(outside, target_is_directory=True)
            with (
                patch.object(cold.control, "ROOT", lab),
                self.assertRaises(RuntimeError),
            ):
                cold.controller_lock()
            (lab / "results").unlink()
            (lab / "results").mkdir()
            target = outside / "other-lock"
            target.touch()
            (lab / "results/controller.lock").symlink_to(target)
            with (
                patch.object(cold.control, "ROOT", lab),
                self.assertRaises(RuntimeError),
            ):
                cold.controller_lock()


if __name__ == "__main__":
    unittest.main()
