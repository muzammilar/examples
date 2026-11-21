"""Statistical boundaries: exclude warmup and reject incomplete/failed cells."""

import unittest

from htap import query_summary, host_rss_bytes


class SummaryTests(unittest.TestCase):
    def records(self):
        return [
            {
                "kind": "analytic-query",
                "phase": "warmup",
                "passed": True,
                "query_fetch_ms": 9999,
                "validation_ms": 0,
            },
            {
                "kind": "phase-start",
                "phase": "measurement",
                "started_ns": 1_000_000_000,
            },
            *[
                {
                    "kind": "analytic-query",
                    "phase": "measurement",
                    "passed": True,
                    "query_fetch_ms": ms,
                    "validation_ms": 0.01,
                }
                for ms in [10, 20, 30]
            ],
            {"kind": "phase-end", "phase": "measurement", "ended_ns": 3_000_000_000},
        ]

    def test_warmup_is_excluded_and_rate_uses_observed_window(self):
        result = query_summary(self.records())
        self.assertEqual(result["queries"], 3)
        self.assertEqual(result["p50_ms"], 20)
        self.assertEqual(result["p99_ms"], 30)
        self.assertEqual(result["queries_per_second"], 1.5)

    def test_direct_monotonic_elapsed_survives_wall_clock_adjustment(self):
        records = self.records()
        records[-1]["ended_ns"] = 500_000_000
        records[-1]["elapsed_ns"] = 2_000_000_000
        result = query_summary(records)
        self.assertEqual(result["measured_seconds"], 2)
        self.assertEqual(result["queries_per_second"], 1.5)
        self.assertEqual(result["phase_timing_clock"], "monotonic")
        records[-1]["elapsed_ns"] = 0
        with self.assertRaisesRegex(RuntimeError, "positive"):
            query_summary(records)

    def test_host_peak_rss_units_are_normalized(self):
        self.assertEqual(host_rss_bytes(4096, "Darwin"), 4096)
        self.assertEqual(host_rss_bytes(4096, "Linux"), 4096 * 1024)

    def test_one_wrong_output_disqualifies_the_cell(self):
        records = self.records()
        records[2]["passed"] = False
        with self.assertRaisesRegex(RuntimeError, "failed"):
            query_summary(records)
        with self.assertRaises(RuntimeError):
            query_summary([])


if __name__ == "__main__":
    unittest.main()
