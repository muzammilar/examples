"""Never invent I/O deltas across reboot or double-count partition aliases."""

import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "file_restore_report", SCRIPTS / "backup_report.py"
)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def guest(boot="old", n=10):
    return {
        "boot_id": boot,
        "diskstats": f"252 0 vda {n} 0 {n*2} 0 {n*3} 0 {n*4} 0 0 {n*5} 0\n252 1 vda1 999 0 999 0 999 0 999 0 0 999 0\n253 0 dm-0 999 0 999 0 999 0 999 0 0 999 0\n",
    }


class EpochMetrics(unittest.TestCase):
    def test_partitions_and_mapper_aliases_excluded(self):
        self.assertEqual(set(report.whole_device_counters(guest())), {"vda"})

    def test_same_boot_sector_counts_are_bytes(self):
        value = report.guest_io(
            {"guests": {"0": guest()}}, {"guests": {"0": guest(n=20)}}
        )
        self.assertEqual(
            value["0"]["whole_device_deltas"]["vda"],
            dict(
                read_ios=10,
                write_ios=30,
                read_bytes=20 * 512,
                write_bytes=40 * 512,
                device_io_ms=50,
            ),
        )

    def test_reboot_cannot_produce_an_io_delta(self):
        value = report.guest_io(
            {"guests": {"0": guest()}}, {"guests": {"0": guest(boot="new", n=20)}}
        )
        self.assertIsNone(value["0"]["whole_device_deltas"]["vda"])


if __name__ == "__main__":
    unittest.main()
