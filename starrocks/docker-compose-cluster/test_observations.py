"""Protect the metrics used to judge availability and correctness during faults."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from production_observations import summarize


class ObservationTests(unittest.TestCase):
    def report(self, reads, writes=()):
        at = datetime(2026, 11, 15, tzinfo=timezone.utc)
        markers = [
            ("before", at),
            ("outage", at + timedelta(seconds=10)),
            ("after", at + timedelta(seconds=20)),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, events in [("writes", writes), ("reads", reads)]:
                (root / name).write_text(
                    "".join(
                        json.dumps(
                            {
                                **event,
                                "at": (
                                    at + timedelta(seconds=event["second"])
                                ).isoformat(),
                            }
                        )
                        + "\n"
                        for event in events
                    )
                )
            return summarize(
                root / "writes", root / "reads", markers, at + timedelta(seconds=30)
            )

    def test_completed_operations_and_failure_latency_stay_separate(self):
        report = self.report(
            [
                {"second": 1, "outcome": "ok", "latency_ms": 40},
                {"second": 10, "outcome": "error", "latency_ms": 1},
                {"second": 11, "outcome": "ok", "latency_ms": 100},
                {"second": 21, "outcome": "ok", "latency_ms": 20},
            ],
            [
                {"second": 12, "stage": "ack", "count": 100, "latency_ms": 50},
                {"second": 13, "stage": "ambiguous", "count": 100, "latency_ms": 2},
            ],
        )
        phase = report["phases"]["outage"]
        self.assertEqual(phase["read_ok_p99_ms"], 100)
        self.assertEqual(phase["read_unsuccessful_p99_ms"], 1)
        self.assertEqual(phase["write_acked_rows"], 100)
        self.assertEqual(phase["write_ack_fraction"], 0.5)
        self.assertEqual(phase["write_ack_p99_ms"], 50)
        self.assertEqual(report["contract_failures"], [])

    def test_wrong_values_and_unrecovered_baseline_fail(self):
        report = self.report(
            [
                {"second": 1, "outcome": "ok", "latency_ms": 5},
                {"second": 11, "outcome": "wrong", "latency_ms": 5},
                {"second": 21, "outcome": "missing", "latency_ms": 5},
            ]
        )
        self.assertEqual(len(report["contract_failures"]), 3)

    def test_an_absent_reader_is_not_passing_coverage(self):
        with self.assertRaisesRegex(RuntimeError, "no observations"):
            self.report([])


if __name__ == "__main__":
    unittest.main()
