"""Keep resource accounting honest across native container restarts."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ResourceReport(unittest.TestCase):
    def test_counter_reset_accumulates_new_lifetime_without_negative_cpu(self):
        rows = [{"type": "environment", "command": ["make", "failover"]}]
        for second, cpu, written in (
            (0, 2_000_000_000, 100),
            (1, 3_000_000_000, 140),
            (2, 500_000_000, 20),
        ):
            rows.append(
                {
                    "type": "resources",
                    "actors": [
                        {
                            "id": "owned",
                            "names": ["/garage0"],
                            "counters": {
                                "read": f"2026-11-27T13:00:0{second}.123456789Z",
                                "cpu_stats": {"cpu_usage": {"total_usage": cpu}},
                                "memory_stats": {"usage": 4096},
                                "blkio_stats": {
                                    "io_service_bytes_recursive": [
                                        {"op": "Write", "value": written}
                                    ]
                                },
                            },
                        }
                    ],
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resources.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            script = Path(__file__).resolve().parents[1] / "scripts/resource_report.py"
            report = json.loads(
                subprocess.check_output(
                    [sys.executable, str(script), str(path)], text=True
                )
            )
        actor = report["actors"][0]
        self.assertEqual(
            actor["counter_deltas"], {"cpu_core_s": 1.5, "disk_write_bytes": 60}
        )
        self.assertEqual(
            actor["counter_resets"], 2
        )  # two counters reset, not two restarts
        self.assertEqual(actor["cpu_utilization_pct_one_core_min"], 100)
        self.assertEqual(actor["cpu_utilization_pct_one_core_max"], 100)
        self.assertNotIn("disk_read_bytes", actor["counter_deltas"])
        self.assertNotIn("disk_write_ios", actor["counter_deltas"])


if __name__ == "__main__":
    unittest.main()
