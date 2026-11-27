"""Do not double-count partitions or claim a CPU delta across the killed MDS."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "projects/kernel-client"))
import recovery_report as metrics


class RecoveryMetrics(unittest.TestCase):
    def test_only_three_whole_osd_disks_are_counted(self):
        lines = [
            f"8 {index * 16} {name} 10 0 20 30 40 0 50 60 0 70 80"
            for index, name in enumerate(("sda", "sdb", "sdc", "sda1", "sdd"))
        ]
        rows = metrics.diskstats({"diskstats": "\n".join(lines)})
        self.assertEqual(set(rows), {"sda", "sdb", "sdc"})
        self.assertEqual(sum(row["read_bytes"] for row in rows.values()), 3 * 20 * 512)
        self.assertEqual(sum(row["write_ms"] for row in rows.values()), 3 * 60)

    def test_missing_osd_counter_is_an_error(self):
        with self.assertRaises(RuntimeError):
            metrics.diskstats({"diskstats": "8 0 sda 10 0 20 30 40 0 50 60 0 70 80"})

    def test_killed_mds_never_gets_a_delta_even_if_new_total_is_larger(self):
        before = {
            "guests": {
                "0": {
                    "sample_monotonic_ns": 1000000000,
                    "units": {
                        "ceph-mds@fs0.service": {"CPUUsageNSec": "100"},
                        "ceph-osd@0.service": {"CPUUsageNSec": "100"},
                    },
                }
            }
        }
        after = {
            "guests": {
                "0": {
                    "sample_monotonic_ns": 2000000000,
                    "units": {
                        "ceph-mds@fs0.service": {"CPUUsageNSec": "500"},
                        "ceph-osd@0.service": {"CPUUsageNSec": "500"},
                    },
                }
            }
        }
        result = metrics.native_daemons(before, after, "fs0")
        self.assertIsNone(result["0/ceph-mds@fs0.service"]["cpu_core_s_delta"])
        self.assertAlmostEqual(
            result["0/ceph-osd@0.service"]["cpu_core_s_delta"], 0.0000004
        )


if __name__ == "__main__":
    unittest.main()
