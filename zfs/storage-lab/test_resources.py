"""Check resource accounting units, unavailable counters and reset exclusions."""

import unittest

import resources


class Resources(unittest.TestCase):
    def sample(self, timestamp, cpu, io, identifier="owned"):
        return {
            "role": "source",
            "id": identifier,
            "counters": {
                "captured_ns": timestamp,
                "cpu.stat": f"usage_usec {cpu}\n",
                "memory.current": "100",
                "memory.peak": "200",
                "state_logical_bytes": 1000,
                "state_allocated_bytes": 4096,
                "io.stat": io,
            },
        }

    def test_byte_and_operation_counters_are_independent(self):
        before = self.sample(
            1_000_000_000, 1000, "254:0 rbytes=0 wbytes=100 rios=0 wios=2"
        )
        after = self.sample(
            3_000_000_000, 1_001_000, "254:0 rbytes=400 wbytes=1100 rios=4 wios=12"
        )
        value = resources.summarize([before, after])["source"]
        self.assertEqual(value["observed_cpu_core_seconds"], 1)
        self.assertEqual(value["average_cpu_percent_one_core_100"], 50)
        self.assertEqual(value["native_block_io"]["254:0"]["wios_per_second"], 5)
        self.assertEqual(value["native_block_io"]["254:0"]["rbytes"], 400)

    def test_missing_device_rows_are_not_zero(self):
        result = resources.summarize([self.sample(1, 0, ""), self.sample(2, 1, "")])[
            "source"
        ]
        self.assertIsNone(result["native_block_io"])
        self.assertEqual(resources.io_fields("254:0\n")["254:0"]["rbytes"], 0)

    def test_counter_reset_and_changed_owner_are_gaps(self):
        before = self.sample(1, 1000, "")
        for after in (
            self.sample(2, 10, ""),
            self.sample(2, 2000, "", identifier="replacement"),
        ):
            result = resources.summarize([before, after])["source"]
            self.assertEqual(result["observed_cpu_core_seconds"], 0)
            self.assertEqual(result["counter_reset_or_identity_gaps"], 1)

    def test_partial_device_record_is_rejected(self):
        for value in ("254:0 rbytes=1", "254:0 rbytes=-1 wbytes=0 rios=0 wios=0"):
            with self.assertRaises(ValueError):
                resources.io_fields(value)


if __name__ == "__main__":
    unittest.main()
