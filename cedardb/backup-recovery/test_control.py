import csv
import json
from pathlib import Path
import tempfile
import unittest

import control
import resources


class EvidenceTests(unittest.TestCase):
    def test_acknowledgement_before_read_requires_visibility_but_future_ack_does_not(
        self,
    ):
        writes = [{"kind": "ok", "id": 100, "ended_ns": 10}]
        before = [{"kind": "ok", "id": 1, "started_ns": 9}]
        self.assertEqual(
            control.check_read_history(writes, before, 2)["successful_reads_checked"], 1
        )
        with self.assertRaisesRegex(RuntimeError, "already acknowledged"):
            control.check_read_history(
                writes, [{"kind": "ok", "id": 99, "started_ns": 11}], 2
            )
        self.assertEqual(
            control.check_read_history(
                writes, [{"kind": "ok", "id": 101, "started_ns": 11}], 2
            )["stale_successes"],
            0,
        )

    def export(self, root, live):
        path = root / "rows.csv"
        with path.open("w") as stream:
            out = csv.writer(stream, lineterminator="\n")
            out.writerow(["id", "customer", "amount", "payload"])
            for identity in [0, 1, *live]:
                out.writerow(
                    [
                        identity,
                        identity % 10000,
                        (identity % 100000) * 17 % 100000,
                        control.body(identity),
                    ]
                )
        return path

    def test_full_body_and_missing_ack_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.export(Path(directory), [1000000000])
            report = control.verify_export(path, 2, {1000000000}, {1000000000})
            self.assertEqual(report["missing_required_acks"], 0)
            with self.assertRaisesRegex(
                RuntimeError, "missing baseline or acknowledged"
            ):
                control.verify_export(
                    path, 2, {1000000000, 1000000001}, {1000000000, 1000000001}
                )
            path.write_text(
                path.read_text().replace("customer-00000|", "customer-00001|", 1)
            )
            with self.assertRaisesRegex(RuntimeError, "wrong body"):
                control.verify_export(path, 2, {1000000000}, {1000000000})
            self.assertTrue(path.exists())

    def test_restore_tail_and_applied_ambiguous_intent_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.export(Path(directory), [1000000000, 1000000001])
            result = control.verify_export(
                path,
                2,
                {1000000000, 1000000001, 1000000002},
                {1000000000, 1000000002},
                required={1000000000},
            )
            self.assertEqual(result["snapshot_excluded_acks"], 1)
            self.assertEqual(result["unacknowledged_intents_present"], 1)

    def test_only_active_reader_may_ignore_incomplete_journal_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.jsonl"
            path.write_text('{"kind":"ok","id":1}\n{"kind":')
            self.assertEqual(len(control.journal(path, active=True)), 1)
            with self.assertRaisesRegex(RuntimeError, "incomplete durable"):
                control.journal(path)

    def test_fast_failures_do_not_improve_success_latency(self):
        events = [
            {"kind": "ok", "started_ns": 0, "elapsed_ns": 10000000},
            {"kind": "fail", "started_ns": 1, "elapsed_ns": 1000},
        ]
        report = control.phase_summary(events, 0, 1000000000)
        self.assertEqual(report["ok"]["p50_ms"], 10)
        self.assertEqual(report["fail"]["p50_ms"], 0.001)
        self.assertIsNone(report["ambiguous"]["p99_ms"])

    def test_resource_counter_reset_is_not_negative_cpu_or_invented_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.jsonl"
            samples = []
            for index, cpu in enumerate([1000000000, 2000000000, 100000000, 300000000]):
                samples.append(
                    {
                        "role": "cedar",
                        "ended_ns": (index + 1) * 1000000000,
                        "stats": {
                            "cpu_stats": {"cpu_usage": {"total_usage": cpu}},
                            "memory_stats": {"usage": 1000},
                        },
                    }
                )
            path.write_text("".join(json.dumps(s) + "\n" for s in samples))
            report = resources.summarize(path)["cedar"]
            self.assertEqual(report["observed_counter_resets"], 1)
            self.assertAlmostEqual(report["observed_cpu_core_seconds"], 1.2)

    def test_null_docker_iops_and_network_counters_remain_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.jsonl"
            samples = [
                {
                    "role": "cedar",
                    "ended_ns": (i + 1) * 1000000000,
                    "stats": {
                        "blkio_stats": {
                            "io_service_bytes_recursive": [
                                {
                                    "major": 254,
                                    "minor": 0,
                                    "op": "read",
                                    "value": i * 1024,
                                }
                            ],
                            "io_serviced_recursive": None,
                        },
                        "networks": None,
                    },
                }
                for i in range(2)
            ]
            path.write_text("".join(json.dumps(item) + "\n" for item in samples))
            summary = resources.summarize(path)["cedar"]
            self.assertIsNone(summary["observed_cpu_core_seconds"])
            metrics = summary["io_and_network"]
            self.assertEqual(metrics["block_254:0_read_bytes"]["observed_delta"], 1024)
            self.assertFalse(
                any("operations" in key or "network" in key for key in metrics)
            )


if __name__ == "__main__":
    unittest.main()
