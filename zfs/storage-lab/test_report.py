"""Do not publish healthy counters over missing or incorrect native evidence."""

import json
from pathlib import Path
import tempfile
import unittest

import report


class ReplayTests(unittest.TestCase):
    def fixture(self, root, actual):
        expected = {"bytes": 10, "sha256": "correct"}
        read = {
            "kind": "read",
            "result": "ok",
            "actual": actual,
            "phase_start": "baseline",
            "phase_end": "baseline",
            "elapsed_ns": 1000,
        }
        (root / "immutable-ack.json").write_text(json.dumps(expected))
        (root / "reader.jsonl").write_text(
            json.dumps(read) + "\n" + json.dumps({"kind": "reader-stopped"}) + "\n"
        )
        (root / "result.json").write_text(
            json.dumps({"read_summary": report.evidence.summarize_reads([read])})
        )

    def test_failed_case_cannot_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "failure.json").write_text("{}")
            with self.assertRaisesRegex(RuntimeError, "failed case"):
                report.case_report(root)

    def test_success_status_with_wrong_body_cannot_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, {"bytes": 10, "sha256": "wrong"})
            with self.assertRaisesRegex(RuntimeError, "body differs"):
                report.case_report(root)

    def test_baseline_only_history_does_not_establish_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, {"bytes": 10, "sha256": "correct"})
            with self.assertRaisesRegex(
                RuntimeError, "missing continuous observation phase"
            ):
                report.case_report(root)

    def phases_fixture(self):
        actions = [
            {
                "command": ["zpool", name],
                "started_monotonic_ns": start,
                "ended_monotonic_ns": end,
                "exit_code": 0,
            }
            for name, start, end in (
                ("offline", 10, 20),
                ("replace", 30, 40),
                ("scrub", 50, 60),
            )
        ]
        records = [
            {
                "kind": "read",
                "result": "ok",
                "phase_start": "baseline",
                "phase_end": "baseline",
                "started_monotonic_ns": start,
                "ended_monotonic_ns": end,
                "elapsed_ns": end - start,
            }
            for start, end in (
                (1, 2),
                (13, 15),
                (22, 23),
                (33, 35),
                (41, 44),
                (51, 55),
                (61, 65),
                (9, 12),
            )
        ]
        return records, actions

    def test_native_intervals_override_lagging_controller_tags(self):
        records, actions = self.phases_fixture()
        summary = report.native_read_phases(records, actions)
        self.assertEqual(summary["baseline"]["ok"], 1)
        self.assertEqual(summary["fault-injection"]["ok"], 1)
        self.assertEqual(summary["spans-transition"]["ok"], 1)
        self.assertEqual(summary["replacement-workflow"]["ok"], 1)

    def test_tagged_repair_read_outside_native_repair_does_not_qualify(self):
        records, actions = self.phases_fixture()
        records = [r for r in records if r["started_monotonic_ns"] != 33]
        with self.assertRaisesRegex(RuntimeError, "actual native phase lacks"):
            report.native_read_phases(records, actions)

    def test_actual_native_recursive_archive_framing_regression(self):
        output = (
            Path(report.__file__).parent / "app/fixtures/recursive-stream-summary.txt"
        ).read_bytes()
        import subprocess

        native = subprocess.CompletedProcess([], 0, output, b"")
        self.assertTrue(
            report.evidence.stream_complete(
                native,
                605513376,
                [
                    "zfslab@evidence_2b48b602218a41de89d5fa916a29662f",
                    "zfslab/data@evidence_2b48b602218a41de89d5fa916a29662f",
                ],
            )
        )
        bad = subprocess.CompletedProcess(
            [], 0, output.replace(b"END records = 4", b"END records = 3"), b""
        )
        self.assertFalse(report.evidence.stream_complete(bad, 605513376))

    def test_publisher_requires_native_fencing_and_actual_cleanup(self):
        cleanup = {
            "healthy_runtime_lists": {
                name: [] for name in ("containers", "volumes", "networks", "image_tag")
            }
        }
        fencing = {
            "source": {"exit_code": 0, "stderr": "", "pools": []},
            "target": {"exit_code": 0, "stderr": "", "pools": ["zfslab"]},
        }
        report.check_final_observations(cleanup, fencing)
        cleanup["healthy_runtime_lists"]["volumes"] = ["still-owned"]
        with self.assertRaisesRegex(RuntimeError, "complete owned cleanup"):
            report.check_final_observations(cleanup, fencing)
        cleanup["healthy_runtime_lists"]["volumes"] = []
        fencing["source"]["exit_code"] = 1
        with self.assertRaisesRegex(RuntimeError, "fencing/target ownership"):
            report.check_final_observations(cleanup, fencing)


if __name__ == "__main__":
    unittest.main()
