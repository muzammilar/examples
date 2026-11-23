"""Failure evidence must survive timeout, and unknown Docker state is not fencing."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import experiment


class ResourceAccounting(unittest.TestCase):
    def test_sample_summary_keeps_memory_mean_peak_and_cpu_separate(self):
        samples = [
            {"time": 1, "cpu_ns": 1000000000, "charged_memory_bytes": 100},
            {"time": 1.5, "cpu_ns": 1200000000, "charged_memory_bytes": 200},
        ]
        report = experiment.resource_summary(samples)
        self.assertEqual(report["sampled_mean_charged_memory_bytes"], 150)
        self.assertEqual(report["sampled_max_charged_memory_bytes"], 200)
        self.assertEqual(report["sampled_peak_cpu_percent_one_core_100"], 40)
        samples[1]["cpu_ns"] = 1
        with self.assertRaises(ValueError):
            experiment.resource_summary(samples)

    def test_cpu_window_uses_actual_container_lifetime_not_polling_delay(self):
        state = {
            "Running": False,
            "StartedAt": "2026-11-21T12:00:00.100000000Z",
            "FinishedAt": "2026-11-21T12:00:01.600000000Z",
        }
        self.assertEqual(experiment.container_seconds({"State": state}), 1.5)
        state["Running"] = True
        with self.assertRaises(ValueError):
            experiment.container_seconds({"State": state})

    def test_sums_device_bytes_and_operations_with_rates(self):
        report = experiment.io_counters(
            "8:16 rbytes=1024 wbytes=2048 rios=4 wios=8 dbytes=0 dios=0\n"
            "8:0 wios=2 rios=1 wbytes=512 rbytes=256\n",
            2,
        )
        self.assertEqual(
            report["totals"], {"rbytes": 1280, "wbytes": 2560, "rios": 5, "wios": 10}
        )
        self.assertEqual(report["per_second"]["rios"], 2.5)
        self.assertEqual(len(report["devices"]), 2)

    def test_unavailable_accounting_is_distinct_from_empty_counter_file(self):
        self.assertFalse(experiment.io_counters("unavailable\n", 1)["available"])
        empty = experiment.io_counters("", 1)
        self.assertTrue(empty["available"])
        self.assertEqual(empty["totals"]["rbytes"], 0)

    def test_kernel_can_print_a_zero_device_without_counter_fields(self):
        report = experiment.io_counters("8:0 \n", 1)
        self.assertEqual(
            report["devices"]["8:0"], {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}
        )

    def test_rejects_incomplete_negative_or_duplicate_device_counters(self):
        for text in (
            "8:0 rbytes=1",
            "8:0 rbytes=-1 wbytes=0 rios=0 wios=0",
            "8:0 rbytes=0 wbytes=0 rios=0 wios=0\n8:0 rbytes=1 wbytes=0 rios=0 wios=0",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                experiment.io_counters(text, 1)

    def test_rates_require_positive_window(self):
        with self.assertRaises(ValueError):
            experiment.io_counters("", 0)


class FailureEvidence(unittest.TestCase):
    def test_image_inventory_errors_never_mean_successful_cleanup(self):
        with patch.object(
            experiment,
            "capture",
            side_effect=subprocess.TimeoutExpired("docker image ls", 30),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                experiment.workload_image_exists()

    def run_failure(self, known):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "runs" / "failure-evidence"
            run.mkdir(parents=True)
            plans = root / "plans"
            plans.mkdir()
            (plans / "rayforce-failure-evidence.tsv").write_text("retained plan")
            state = {
                "Id": "owned-container",
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": experiment.PROJECT,
                        "com.docker.compose.project.working_dir": str(experiment.ROOT),
                    }
                },
                "State": {"Running": True},
            }
            error = subprocess.TimeoutExpired(
                "Docker resources" if known else "docker inspect", 30
            )
            captures = ([json.dumps([state])] if known else [error]) + [
                "partial native output\n"
            ]
            with (
                patch.object(experiment, "RESULTS", root),
                patch.object(experiment, "command") as command,
                patch.object(experiment, "capture", side_effect=captures),
                patch.object(experiment, "docker_socket", return_value="owned-socket"),
                patch.object(experiment, "resource_sample", side_effect=error),
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
