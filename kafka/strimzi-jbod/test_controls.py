"""Fail-closed native recovery parsing; no Kubernetes dependencies."""

import copy
import unittest
from unittest.mock import patch

import control


class NativeTests(unittest.TestCase):
    def test_description(self):
        text = "Topic: orders-x Partition: 0 Leader: 3 Replicas: 3,4,5 Isr: 3,4,5"
        row = control.partitions(text)[0]
        self.assertEqual(row["leader"], 3)
        self.assertEqual(row["isr"], [3, 4, 5])
        with self.assertRaises(ValueError):
            control.partitions(text + "\n" + text)
        with self.assertRaises(ValueError):
            control.partitions("transport failure")

    def test_json_rejects_transport_error(self):
        with self.assertRaises(ValueError):
            control.embedded_json("connection refused")
        self.assertEqual(
            control.embedded_json('diagnostic\n{"brokers":[]}'), {"brokers": []}
        )

    @patch.object(control, "topics", return_value=["orders-x"])
    def test_full_replica_health(self, _):
        snapshot = {
            "partitions": [
                {
                    "topic": "orders-x",
                    "partition": p,
                    "leader": 3,
                    "replicas": [3, 4, 5],
                    "isr": [3, 4, 5],
                }
                for p in range(6)
            ],
            "identities": {str(i): {"ready": True} for i in range(6)},
            "quorum": 'LeaderId: 0\nCurrentVoters: [{"id":0},{"id":1},{"id":2}]\nMaxFollowerLag: 0',
            "log_dirs": {
                "brokers": [
                    {
                        "broker": b,
                        "logDirs": [
                            {
                                "error": None,
                                "partitions": [
                                    {
                                        "partition": "orders-x-" + str(p),
                                        "offsetLag": 0,
                                        "isFuture": False,
                                    }
                                    for p in range(6)
                                ],
                            }
                        ],
                    }
                    for b in [3, 4, 5]
                ]
            },
        }
        self.assertTrue(control.healthy(snapshot))
        changes = [("partitions", 0, "isr", [3, 4]), ("partitions", 0, "leader", -1)]
        for key, index, field, value in changes:
            broken = copy.deepcopy(snapshot)
            broken[key][index][field] = value
            self.assertFalse(control.healthy(broken))
        for field, value in [("offsetLag", 1), ("isFuture", True)]:
            broken = copy.deepcopy(snapshot)
            broken["log_dirs"]["brokers"][1]["logDirs"][0]["partitions"][0][field] = (
                value
            )
            self.assertFalse(control.healthy(broken))
        broken = copy.deepcopy(snapshot)
        broken["log_dirs"]["brokers"][1]["logDirs"][0]["error"] = (
            "KafkaStorageException"
        )
        self.assertFalse(control.healthy(broken))
        broken = copy.deepcopy(snapshot)
        broken["partitions"].pop()
        self.assertFalse(control.healthy(broken))


class LiveWitnessTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.plan = {"minimum_messages": 2, "run_id": "run"}
        self.phases = [
            {"phase": "baseline", "time": 1},
            {"phase": "complete", "time": 9},
        ]
        self.intent = {
            "stage": "intent",
            "time": 2,
            "seq": 0,
            "id": "run-order-0",
            "sha256": "ab" * 32,
            "created_ms": 2000,
            "bytes": 42,
        }
        self.ack = self.intent | {
            "stage": "ack",
            "time": 3,
            "seconds": 0.1,
            "partition": 0,
            "offset": 0,
        }
        self.message = self.intent | {
            "stage": "message",
            "time": 4,
            "seconds": 0.01,
            "partition": 0,
            "offset": 0,
        }

    def tearDown(self):
        self.directory.cleanup()

    def write(self, producer, reader):
        import gzip
        import json

        for name, rows in [("producer", producer), ("reader", reader)]:
            with gzip.open(self.root / (name + ".jsonl.gz"), "wt") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")

    def run_report(self):
        import report

        return report.reconcile(self.root, self.plan, self.phases)

    def test_complete_body_and_position(self):
        self.write([self.intent, self.ack], [self.message])
        result = self.run_report()
        self.assertEqual(result["live_observed_acknowledged"], 1)
        self.assertEqual(result["acknowledged_loss"], 0)
        self.assertEqual(result["phases"]["baseline"]["write_ack"]["p99_ms"], 100)
        for change in [{"sha256": "ba" * 32}, {"offset": 1}, {"id": "foreign-order-0"}]:
            self.write([self.intent, self.ack], [self.message | change])
            with self.assertRaises(ValueError):
                self.run_report()

    def test_persisted_ack_with_unknown_offset(self):
        ack = self.ack | {
            "offset": None,
            "offset_known": False,
            "persistence_status": 2,
        }
        self.write([self.intent, ack], [self.message])
        result = self.run_report()
        self.assertEqual(result["acknowledged_without_native_offset"], 1)
        self.assertEqual(result["live_observed_acknowledged"], 1)
        self.write([self.intent, ack], [self.message, self.message | {"offset": 1}])
        with self.assertRaises(ValueError):
            self.run_report()
        self.write([self.intent, ack | {"persistence_status": 1}], [self.message])
        with self.assertRaises(ValueError):
            self.run_report()

    def test_missing_acknowledged_is_failure(self):
        self.write([self.intent, self.ack], [])
        with self.assertRaises(ValueError):
            self.run_report()

    def test_ambiguous_visibility_stays_nonacknowledged(self):
        self.write([self.intent, self.ack | {"stage": "ambiguous"}], [self.message])
        result = self.run_report()
        self.assertEqual(result["acknowledged"], 0)
        self.assertEqual(result["ambiguous"], 1)
        self.assertEqual(result["live_visible_nonack"], 1)

    def test_rejected_and_duplicate_logical_writes_fail(self):
        self.write([self.intent, self.ack | {"stage": "rejected"}], [self.message])
        with self.assertRaises(ValueError):
            self.run_report()
        self.write(
            [self.intent, self.ack], [self.message, self.message | {"offset": 1}]
        )
        with self.assertRaises(ValueError):
            self.run_report()
        self.write([self.intent, self.ack], [self.message, self.message])
        self.assertEqual(self.run_report()["duplicate_live_deliveries"], 1)

    def test_truncated_journal_fails(self):
        self.write([self.intent, self.ack], [self.message])
        path = self.root / "producer.jsonl.gz"
        path.write_bytes(path.read_bytes()[:-5])
        with self.assertRaises(EOFError):
            self.run_report()


class ResourceTests(unittest.TestCase):
    def test_native_cpu_and_sampled_memory(self):
        import tempfile
        import gzip
        import json
        from pathlib import Path
        import resources

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.gz"

            def event(cpu, memory):
                return {
                    "time": cpu,
                    "node": "worker",
                    "stats": {
                        "stats": [
                            {
                                "attributes": {
                                    "id": "a",
                                    "metadata": {"name": "kafka"},
                                    "labels": {},
                                },
                                "cpu": {"usageCoreNanoSeconds": {"value": str(cpu)}},
                                "memory": {"rssBytes": {"value": str(memory)}},
                            }
                        ]
                    },
                }

            with gzip.open(path, "wt") as stream:
                stopped = {
                    "time": 30,
                    "node": "worker",
                    "stats": {
                        "stats": [
                            {
                                "attributes": {
                                    "id": "stopped",
                                    "metadata": {"name": "verify"},
                                }
                            }
                        ]
                    },
                }
                for row in [event(10, 20), event(20, 10), stopped]:
                    stream.write(json.dumps(row) + "\n")
            result = resources.summarize(path)
            self.assertEqual(result["containers"]["a"]["sampled_max_rssBytes"], 20)
            self.assertEqual(result["observed_total_cpu_core_seconds"], 10e-9)
            self.assertEqual(result["samples_without_cpu_by_container"], {"stopped": 1})
            with gzip.open(path, "at") as stream:
                stream.write(json.dumps(event(1, 20)) + "\n")
            with self.assertRaises(ValueError):
                resources.summarize(path)


class FailedStateTests(unittest.TestCase):
    def test_diagnostic_cannot_be_mistaken_for_a_strict_pass(self):
        proof = {key: 0 for key in control.DIAGNOSTIC_STATE_FIELDS}
        proof.update(
            {
                "diagnostic_only": True,
                "strict_verification": False,
                "acknowledged": 1,
                "observed_acknowledged": 1,
                "positions": {"0": 3},
                "end_offsets": {"0": 3},
                "poll_errors": 0,
            }
        )
        state = control.diagnostic_state(proof)
        self.assertEqual(state["acknowledged"], 1)
        for change in [
            {"strict_verification": True},
            {"diagnostic_only": False},
            {"acknowledged_loss": 1},
            {"corruption": 1},
            {"duplicate_acknowledged_records": 1},
            {"poll_errors": 1},
            {"positions": {"0": 2}},
            {"observed_acknowledged": 0},
        ]:
            with self.assertRaises(RuntimeError):
                control.diagnostic_state(proof | change)


if __name__ == "__main__":
    unittest.main()
