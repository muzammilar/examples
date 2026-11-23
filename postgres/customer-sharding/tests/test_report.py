import json
from pathlib import Path
import tempfile
import unittest

from report import observations


class PhaseEvidence(unittest.TestCase):
    def test_failures_do_not_improve_success_latency_and_wrong_reads_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = {"shard": 0, "started": "2026-11-16T00:00:00Z", "count": 100}
            writes = [
                base
                | {"stage": "ack", "at": "2026-11-16T00:00:03Z", "latency_ms": 500},
                base
                | {
                    "stage": "error",
                    "at": "2026-11-16T00:00:04Z",
                    "latency_ms": 1,
                    "ambiguous": True,
                },
            ]
            reads = [
                base
                | {"outcome": "missing", "at": "2026-11-16T00:00:03Z", "latency_ms": 2},
                base
                | {"outcome": "ok", "at": "2026-11-16T00:00:05Z", "latency_ms": 20},
            ]
            for name, rows in (("writes", writes), ("reads", reads)):
                (root / (name + ".jsonl")).write_text(
                    "".join(json.dumps(r) + "\n" for r in rows)
                )
            result = observations(
                root,
                [
                    {"phase": "before", "at": "2026-11-16T00:00:00Z"},
                    {"phase": "outage", "at": "2026-11-16T00:00:02Z"},
                ],
                "2026-11-16T00:00:06Z",
            )
            shard = result["phases"][1]["shards"]["0"]
            self.assertEqual(shard["write_ok"]["p99_ms"], 500)
            self.assertEqual(shard["write_errors"]["p99_ms"], 1)
            self.assertEqual(shard["ambiguous_commit_responses"], 1)
            self.assertEqual(result["phases"][0]["shards"]["0"]["write_ok"]["count"], 0)
            self.assertEqual(result["contract_failures"], 1)
