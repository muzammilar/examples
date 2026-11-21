"""Reject flattering summaries and truncated windows before publication."""

import unittest

from htap import query_summary
from htap_report import (
    verify_query_evidence,
    workload_comparisons,
    verify_resource_evidence,
)
from resources import summarize_records
import test_htap


class PublicationTests(unittest.TestCase):
    def records(self):
        return [
            {"kind": "phase-start", "phase": "warmup", "started_ns": 0},
            {"kind": "phase-end", "phase": "warmup", "ended_ns": 1_000_000_000},
            *test_htap.SummaryTests().records(),
        ]

    def test_rejects_inflated_rate_and_truncated_windows(self):
        records = self.records()
        summary = query_summary(records)
        verify_query_evidence(records, summary, 1, 2)
        with self.assertRaisesRegex(RuntimeError, "raw evidence"):
            verify_query_evidence(records, summary | {"queries_per_second": 999}, 1, 2)
        with self.assertRaisesRegex(RuntimeError, "measurement window"):
            verify_query_evidence(records, summary, 1, 3)
        with self.assertRaisesRegex(RuntimeError, "warmup window"):
            verify_query_evidence(records, summary, 2, 2)

    def test_throughput_and_resource_advantages_stay_separate(self):
        summaries = [
            {
                "backend": backend,
                "concurrent": live,
                "queries_per_second_median": rate,
                "median_observed_db_cpu_core_seconds_per_query": cpu,
                "median_sampled_db_mean_memory_bytes": memory,
            }
            for backend, live, rate, cpu, memory in [
                ("cedar", False, 100, 2, 300),
                ("postgres", False, 50, 1, 200),
                ("cedar", True, 80, 2, 300),
                ("postgres", True, 50, 1, 200),
            ]
        ]
        comparisons, impact = workload_comparisons(summaries)
        self.assertEqual(comparisons[0]["higher_median_throughput_backend"], "cedar")
        self.assertEqual(comparisons[0]["median_throughput_ratio"], 2)
        self.assertEqual(
            comparisons[0]["lower_observed_db_cpu_per_query_backend"], "postgres"
        )
        self.assertEqual(comparisons[0]["lower_sampled_db_memory_backend"], "postgres")
        self.assertAlmostEqual(impact[0]["concurrent_query_rate_change_percent"], -20)
        summaries[1]["queries_per_second_median"] = 100
        self.assertEqual(
            workload_comparisons(summaries)[0][0]["higher_median_throughput_backend"],
            "tie",
        )

    def test_resource_counter_tampering_and_missing_roles_are_rejected(self):
        samples = [
            {
                "role": role,
                "started_ns": index * 1_000_000_000,
                "ended_ns": index * 1_000_000_000 + 100,
                "stats": {
                    "cpu_stats": {"cpu_usage": {"total_usage": index * 1_000_000_000}},
                    "memory_stats": {"usage": 1024},
                },
            }
            for role in ("cedar", "analytics")
            for index in (1, 2)
        ]
        summary = summarize_records(samples)
        verify_resource_evidence(samples, summary, 0, 3_000_000_000, "cedar", False)
        with self.assertRaisesRegex(RuntimeError, "required workload role"):
            verify_resource_evidence(samples, summary, 0, 3_000_000_000, "cedar", True)
        summary["cedar"]["observed_cpu_core_seconds"] *= 2
        with self.assertRaisesRegex(RuntimeError, "raw measurement samples"):
            verify_resource_evidence(samples, summary, 0, 3_000_000_000, "cedar", False)

    def test_small_utc_shortfall_requires_monotonic_evidence(self):
        records = self.records()
        records[0]["duration_s"] = 1
        records[3]["duration_s"] = 2
        records[-1]["ended_ns"] -= 3_000_000
        summary = query_summary(records)
        with self.assertRaisesRegex(RuntimeError, "measurement window"):
            verify_query_evidence(records, summary, 1, 2)
        records.append({"kind": "process-resources", "main_wall_seconds": 3.01})
        audit = verify_query_evidence(records, summary, 1, 2)
        self.assertAlmostEqual(audit["measurement_utc_shortfall_ms"], 3)
        records[-1]["main_wall_seconds"] = 2.99
        with self.assertRaisesRegex(RuntimeError, "measurement window"):
            verify_query_evidence(records, summary, 1, 2)
        records[-1]["main_wall_seconds"] = 3.01
        records[-2]["ended_ns"] -= 20_000_000
        with self.assertRaisesRegex(RuntimeError, "measurement window"):
            verify_query_evidence(records, query_summary(records), 1, 2)

    def test_failed_raw_query_overrides_a_passing_summary(self):
        records = self.records()
        summary = query_summary(records)
        records[4]["passed"] = False
        with self.assertRaisesRegex(RuntimeError, "failed"):
            verify_query_evidence(records, summary, 1, 2)


if __name__ == "__main__":
    unittest.main()
