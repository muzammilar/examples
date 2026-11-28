import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from resource_report import parse, memory_bytes


class ResourceTests(unittest.TestCase):
    def test_multiple_devices_are_aggregated(self):
        counters = parse(
            "METRIC:cpu.stat\nusage_usec 100\nMETRIC:io.stat\n8:0 rbytes=12 wbytes=34 rios=2 wios=3\n8:16 rbytes=7 wbytes=8 rios=1 wios=1\n"
        )
        self.assertEqual(
            counters, dict(cpu_us=100, rbytes=19, wbytes=42, rios=3, wios=4)
        )

    def test_missing_is_not_zero(self):
        self.assertNotIn("rbytes", parse("METRIC:cpu.stat\nusage_usec 100\n"))

    def test_counter_blank_lines_and_empty_io(self):
        self.assertEqual(
            parse("METRIC:cpu.stat\nusage_usec 12\n\nMETRIC:io.stat\n"),
            dict(cpu_us=12, rbytes=0, wbytes=0, rios=0, wios=0),
        )

    def test_units(self):
        self.assertEqual(memory_bytes("1.5GiB"), 1.5 * 1024**3)
        self.assertEqual(memory_bytes("12MB"), 12000000)
        with self.assertRaises(ValueError):
            memory_bytes("12unknown")
