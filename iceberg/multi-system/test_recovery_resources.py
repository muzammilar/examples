import unittest

import recovery_resources as resources


class NativeCounterTests(unittest.TestCase):
    def test_absent_io_is_unavailable_but_bare_device_is_native_zero(self):
        self.assertEqual(resources.io_fields(""), {})
        self.assertEqual(
            resources.io_fields("254:0"),
            {"254:0": {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}},
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            resources.io_fields("254:0 rbytes=10")

    def test_restart_never_becomes_a_positive_cpu_or_io_delta(self):
        def row(start, usage, pid):
            return {
                "role": "storage",
                "container_id": "same-container",
                "available": True,
                "counters": {
                    "native_uptime_start_ns": start,
                    "pid1_start_ticks": pid,
                    "cpu": {"usage_usec": usage, "user_usec": usage, "system_usec": 0},
                    "charged_memory_bytes": 10,
                    "lifetime_peak_memory_bytes": 20,
                    "allocated_disk_bytes": 100,
                    "io": {},
                },
            }

        result = resources.summarize(
            [row(1, 100, 1), row(1000000001, 500, 2), row(2000000001, 1500, 2)]
        )["storage"]
        self.assertEqual(result["observed_seconds"], 1)
        self.assertEqual(result["cpu_core_seconds"]["total"], 0.001)
        self.assertEqual(result["identity_or_counter_gaps"], 1)
        self.assertIsNone(result["native_block_io"])

    def test_transport_failure_is_unavailable_not_zero_usage(self):
        result = resources.summarize(
            [
                {
                    "role": "storage",
                    "container_id": "owned",
                    "available": False,
                    "error": "runtime transport failed",
                }
            ]
        )["storage"]
        self.assertEqual(result["unavailable_samples"], 1)
        self.assertIsNone(result["cpu_percent_one_core_100"])
        self.assertIsNone(result["cpu_core_seconds"])
        self.assertIsNone(result["charged_memory_bytes_min_mean_max"])

    def test_partial_disk_total_after_failed_walk_is_unavailable(self):
        self.assertEqual(resources.allocated_bytes("123\t/data"), 125952)
        self.assertIsNone(resources.allocated_bytes("123\t/data\nunavailable"))
        self.assertIsNone(resources.allocated_bytes("unavailable"))

    def test_all_unavailable_metrics_cannot_qualify_a_healthy_phase(self):
        records = [
            {
                "role": "storage",
                "host_started_ns": 10,
                "container_id": "owned",
                "available": False,
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "healthy-phase resource coverage"):
            resources.require_coverage(records, ["storage"], 1)

    def test_native_probe_uses_proc_uptime_and_rejects_partial_disk_output(self):
        fields = {
            "started_uptime": "1.23 4.56",
            "ended_uptime": "1.25 4.56",
            "pid_stat": "1 (owned process) " + " ".join(["S"] + ["0"] * 18 + ["42"]),
            "cpu.stat": "usage_usec 3\nuser_usec 2\nsystem_usec 1",
            "memory.current": "1024",
            "memory.peak": "2048",
            "memory.events": "oom 0",
            "io.stat": "254:0",
            "network": "eth0: " + " ".join(["0"] * 16),
            "allocated_kib": "123\t/data\nunavailable",
        }
        parsed = resources.parse_probe(
            "\n".join("!" + key + "\n" + value for key, value in fields.items())
        )
        self.assertEqual(parsed["native_uptime_start_ns"], 1230000000)
        self.assertEqual(parsed["native_uptime_end_ns"], 1250000000)
        self.assertEqual(parsed["pid1_start_ticks"], 42)
        self.assertIsNone(parsed["allocated_disk_bytes"])
        self.assertNotIn("memory_stat", parsed)  # Historical probes remain replayable.
        fields["memory.stat"] = "anon 700\nfile 300\nkernel 24\npgfault 40"
        parsed = resources.parse_probe(
            "\n".join("!" + key + "\n" + value for key, value in fields.items())
        )
        self.assertEqual(parsed["memory_stat"]["file"], 300)
        record = {
            "role": "storage",
            "container_id": "owned",
            "available": True,
            "counters": parsed,
        }
        report = resources.summarize([record])["storage"]
        self.assertEqual(report["memory_stat_samples"], 1)
        self.assertEqual(
            report["memory_stat_bytes_min_mean_max"]["anon"], [700, 700, 700]
        )
        self.assertEqual(
            report["memory_stat_bytes_min_mean_max"]["kernel"], [24, 24, 24]
        )
        fields["memory.stat"] = "anon -1"
        with self.assertRaisesRegex(ValueError, "negative"):
            resources.parse_probe(
                "\n".join("!" + key + "\n" + value for key, value in fields.items())
            )


if __name__ == "__main__":
    unittest.main()
