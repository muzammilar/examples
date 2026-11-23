import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from recovery_report import compression_summary, latency, operation_summary


class ReportTests(unittest.TestCase):
    def test_compression_requires_bound_file_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "verifications" / "verification-id"
            output.mkdir(parents=True)
            storage = {
                "passed": True,
                "run_id": "run",
                "verification_id": "verification-id",
                "config_sha256": "hash",
                "snapshot": {"snapshot_id": 42},
                "totals": {"files": 1},
                "encoded_parquet_chunk_compression_ratio": 2.5,
            }
            path = output / "storage-metrics.json"
            path.write_text(json.dumps(storage))
            proof = {
                key: storage[key]
                for key in ("run_id", "verification_id", "config_sha256", "snapshot")
            }
            proof.update(
                passed=True,
                qualified=True,
                storage_metrics={
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "snapshot": storage["snapshot"],
                },
            )
            (output / "verified.json").write_text(json.dumps(proof))
            self.assertTrue(compression_summary(root)["measured"])
            storage["snapshot"] = {"snapshot_id": 43}
            path.write_text(json.dumps(storage))
            with self.assertRaisesRegex(ValueError, "cross-snapshot"):
                compression_summary(root)

    def test_nearest_rank_and_sample_count_do_not_mix_fast_failures(self):
        self.assertEqual(latency([1_000_000, 3_000_000, 2_000_000])["p50_ms"], 2)
        self.assertEqual(latency([1_000_000, 3_000_000])["p99_ms"], 3)
        self.assertIsNone(latency([]))

    def test_cross_phase_outcomes_and_pending_intents_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "case-id"
            root = case / "recovery-case-id"
            root.mkdir(parents=True)
            markers = [
                {"phase": p, "host_ns": ns}
                for p, ns in (
                    ("baseline", 1),
                    ("fault", 2_000_000_001),
                    ("recovered", 4_000_000_001),
                )
            ]
            (case / "phases.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in markers)
            )
            intent = {"kind": "intent", "phase": "baseline", "operation_id": "a"}
            ok = {
                **intent,
                "kind": "outcome",
                "ended_phase": "baseline",
                "result": "ok",
                "first_id": 0,
                "end_id": 10,
                "duration_ns": 4_000_000,
            }
            failed = {
                **ok,
                "operation_id": "b",
                "ended_phase": "fault",
                "result": "ambiguous",
                "duration_ns": 1_000_000,
            }
            events = [
                intent,
                ok,
                {**intent, "operation_id": "b"},
                failed,
                {**intent, "operation_id": "c"},
            ]
            (root / "writer-000.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in events) + '{"torn":'
            )
            report = operation_summary(case)
            self.assertEqual(
                report["phases"]["baseline"]["successful_request_rates_per_second"][
                    "write"
                ],
                0.5,
            )
            self.assertEqual(
                report["phases"]["fault"]["counts"]["write_completed_ambiguous"], 1
            )
            self.assertEqual(
                report["pending_write_intents"],
                [
                    {
                        "operation_id": "c",
                        "phase": "baseline",
                        "result": "ambiguous_no_outcome",
                    }
                ],
            )
            self.assertEqual(
                report["journal_status"]["writer-000.jsonl"], "incomplete_final_line"
            )
            self.assertEqual(len(report["latencies"]), 2)
            self.assertFalse(report["compression"]["measured"])


if __name__ == "__main__":
    unittest.main()
