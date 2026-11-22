"""Host controls for native measurements and failure-preserving Spark teardown."""

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import compact
import resources


def record(at, pid=123, generation=10, usage=0, *, unavailable=False, devices=None):
    process = {
        "available": not unavailable,
        "value": {
            "pid": pid,
            "start_ticks": generation,
            "cpu_ticks": {"user": usage, "system": 0},
            "rss_bytes": 100,
            "peak_rss_bytes": 200,
            "io": {
                "available": True,
                "value": {"read_bytes": usage, "write_bytes": usage * 2},
            },
        },
    }
    scalar = {"available": True, "value": 100}
    return {
        "monotonic_ns": at,
        "python": process,
        "jvm": process,
        "container": {
            "cpu": {"available": True, "value": {"usage_usec": usage * 10000}},
            "io": {"available": True, "value": {} if devices is None else devices},
            "memory_current_bytes": scalar,
            "memory_peak_bytes": scalar,
        },
    }


class NativeResources(unittest.TestCase):
    def test_native_process_pid_generation_cpu_io_rss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "123"
            proc.mkdir()
            values = ["0"] * 22
            values[0], values[11], values[12], values[19], values[21] = (
                "S",
                "31",
                "7",
                "999",
                "4",
            )
            (proc / "stat").write_text("123 (java worker) " + " ".join(values))
            (proc / "status").write_text("VmHWM:\t40 kB\n")
            (proc / "io").write_text(
                "rchar: 100\nwchar: 100\nread_bytes: 0\nwrite_bytes: 50\n"
            )
            value = resources.process(123, root)
            self.assertTrue(value["available"])
            self.assertEqual(value["value"]["start_ticks"], 999)
            self.assertEqual(value["value"]["cpu_ticks"], {"user": 31, "system": 7})
            self.assertEqual(value["value"]["peak_rss_bytes"], 40960)
            self.assertEqual(value["value"]["io"]["value"]["read_bytes"], 0)
            (proc / "io").write_text("")
            self.assertFalse(resources.process(123, root)["value"]["io"]["available"])
            (proc / "io").unlink()
            self.assertFalse(resources.process(123, root)["value"]["io"]["available"])
            (proc / "stat").unlink()
            self.assertFalse(resources.process(123, root)["available"])

    def test_empty_io_is_unavailable_but_native_device_zero_is_zero(self):
        self.assertEqual(resources.io_devices(""), {})
        self.assertEqual(
            resources.io_devices("254:0"),
            {"254:0": {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}},
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            resources.io_devices("254:0 rbytes=2")
        result = resources.summarize([record(0), record(1000000000, usage=5)])
        self.assertIsNone(result["container"]["io"])
        zero = resources.io_devices("254:0")
        result = resources.summarize(
            [record(0, devices=zero), record(1000000000, usage=5, devices=zero)]
        )
        self.assertEqual(result["container"]["io"]["254:0"]["per_second"]["rbytes"], 0)

    def test_native_process_generation_change_never_becomes_cpu_or_io_work(self):
        result = resources.summarize(
            [
                record(0, usage=10),
                record(1000000000, generation=11, usage=1000),
                record(2000000000, generation=11, usage=1100),
            ]
        )
        expected = 100 / resources.os.sysconf("SC_CLK_TCK")
        self.assertAlmostEqual(result["jvm"]["cpu_core_seconds"], expected)
        self.assertEqual(result["jvm"]["observed_seconds"], 1)
        self.assertEqual(result["jvm"]["io"]["process"]["counters"]["read_bytes"], 100)
        self.assertEqual(result["jvm"]["identity_or_counter_gaps"], 1)
        self.assertEqual(len(result["jvm"]["generations"]), 2)

    def test_quantized_short_intervals_do_not_publish_false_process_peaks(self):
        with patch.object(resources.os, "sysconf", return_value=100):
            short = resources.summarize([record(0), record(1000000, usage=1)])
            whole = resources.summarize(
                [record(0), record(1000000, usage=1), record(1000000000, usage=4)]
            )
        for role in ("python", "jvm"):
            self.assertIsNone(short[role]["cpu_interval_percent_min_mean_max"])
            self.assertIsNone(short[role]["cpu_percent_one_core_100"])
            self.assertAlmostEqual(short[role]["cpu_core_seconds"], 0.01)
            self.assertEqual(short[role]["cpu_short_intervals_suppressed"], 1)
            self.assertAlmostEqual(whole[role]["cpu_core_seconds"], 0.04)
            self.assertAlmostEqual(whole[role]["cpu_percent_one_core_100"], 4)
            self.assertAlmostEqual(
                whole[role]["cpu_interval_percent_min_mean_max"]["max"],
                100 * 0.03 / 0.999,
            )
            self.assertEqual(whole[role]["cpu_short_intervals_suppressed"], 1)
            self.assertEqual(whole[role]["cpu_utilization_min_interval_seconds"], 0.1)
        # Cgroup microsecond counters do not inherit the process tick threshold.
        self.assertEqual(short["container"]["cpu_short_intervals_suppressed"], 0)
        self.assertIsNotNone(short["container"]["cpu_interval_percent_min_mean_max"])

    def test_missing_process_observations_are_not_zero_usage(self):
        result = resources.summarize([record(0, unavailable=True)])
        for role in ("jvm", "python"):
            self.assertIsNone(result[role]["cpu_core_seconds"])
            self.assertIsNone(result[role]["sampled_memory_bytes_min_mean_max"])
            self.assertIsNone(result[role]["io"])

    def test_live_jvm_must_be_observed_and_sampler_failure_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler = resources.Sampler(Path(directory) / "samples.jsonl")
            try:
                with patch.object(
                    resources, "snapshot", return_value={"jvm": {"available": False}}
                ):
                    with self.assertRaisesRegex(RuntimeError, "live native JVM"):
                        sampler.attach_jvm(4321)
                    sampler.errors.append(OSError("sampling failed"))
                    with self.assertRaisesRegex(
                        RuntimeError, "resource sampler failed"
                    ):
                        sampler.close()
            finally:
                sampler.stream.close()


class FailureReports(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.dict(
                compact.os.environ,
                {"TABLE": "lab.probe", "TARGET_FILE_BYTES": "1048576"},
            )
        )
        self.spark = Mock()
        self.spark.sparkContext._jvm.java.lang.management.ManagementFactory.getRuntimeMXBean.return_value.getPid.return_value = 4321
        self.sampler = Mock()
        self.sampler.close.return_value = {
            "jvm": {"generations": [{"pid": 4321, "start_ticks": 99}]}
        }
        self.stack.enter_context(
            patch.object(compact, "Sampler", return_value=self.sampler)
        )
        self.build = self.stack.enter_context(
            patch.object(compact, "build_spark", return_value=self.spark)
        )
        self.work = self.stack.enter_context(
            patch.object(
                compact,
                "compact",
                side_effect=lambda spark, name, target, result: result.update(
                    table=name, seconds=1
                ),
            )
        )

    def report(self):
        return json.loads(next(self.output.glob("*-resources.json")).read_text())

    def test_success_keeps_legacy_output_and_unique_fsynced_reports(self):
        first, second = compact.run(self.output), compact.run(self.output)
        self.assertNotEqual(first, second)
        self.assertEqual(len(list(self.output.glob("*-resources.json"))), 2)
        self.assertTrue(self.report()["passed"])
        self.assertEqual(
            json.loads((self.output / "lab-probe-compaction.json").read_text())[
                "seconds"
            ],
            1,
        )
        self.assertEqual(self.spark.stop.call_count, 2)
        self.sampler.attach_jvm.assert_called_with(4321)
        self.assertTrue(self.report()["stop"]["attempted"])
        self.assertIn("spark_startup_started_ns", self.report()["phases"])
        self.assertFalse(self.report()["compression"]["available"])

    def test_startup_failure_is_recorded_without_missing_session_cleanup(self):
        failure = RuntimeError("Spark startup failed")
        self.build.side_effect = failure
        with self.assertRaises(RuntimeError) as caught:
            compact.run(self.output)
        self.assertIs(caught.exception, failure)
        self.assertFalse(self.report()["passed"])
        self.assertFalse(self.report()["stop"]["attempted"])
        self.sampler.close.assert_called_once()
        self.spark.stop.assert_not_called()

    def test_work_failure_still_attempts_stop_and_preserves_original_error(self):
        original = ValueError("bad retained snapshot")
        self.work.side_effect = original
        self.spark.stop.side_effect = RuntimeError("stop failed too")
        with self.assertRaises(ValueError) as caught:
            compact.run(self.output)
        self.assertIs(caught.exception, original)
        self.spark.stop.assert_called_once()
        self.assertFalse(self.report()["passed"])
        self.assertFalse(self.report()["stop"]["passed"])
        self.assertEqual(len(self.report()["errors"]), 2)

    def test_pre311_exception_without_add_note_preserves_primary(self):
        class LegacyError(ValueError):
            @property
            def add_note(self):
                raise AttributeError("not available on older Python")

        original = LegacyError("original failure")
        self.work.side_effect = original
        self.spark.stop.side_effect = RuntimeError("secondary failure")
        with self.assertRaises(LegacyError) as caught:
            compact.run(self.output)
        self.assertIs(caught.exception, original)
        self.assertEqual(len(self.report()["errors"]), 2)
        self.assertFalse(self.report()["passed"])

    def test_pre_stop_sampler_error_never_prevents_stop_attempt(self):
        self.sampler.capture.side_effect = OSError("sample failed")
        with self.assertRaisesRegex(OSError, "sample failed"):
            compact.run(self.output)
        self.spark.stop.assert_called_once()
        self.assertTrue(self.report()["stop"]["passed"])
        self.assertFalse(self.report()["passed"])

    def test_stop_failure_after_correct_work_cannot_pass(self):
        self.spark.stop.side_effect = RuntimeError("stop failed")
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            compact.run(self.output)
        self.assertFalse(self.report()["passed"])
        self.sampler.close.assert_called_once()

    def test_sampler_failure_cannot_pass(self):
        self.sampler.close.side_effect = RuntimeError("metrics failed")
        with self.assertRaisesRegex(RuntimeError, "metrics failed"):
            compact.run(self.output)
        self.assertFalse(self.report()["passed"])
        self.assertEqual(self.report()["errors"][-1]["stage"], "resource-finalization")

    def test_report_failure_is_fatal_and_never_masks_primary_work_failure(self):
        for primary in (None, ValueError("original operation failed")):
            with self.subTest(primary=primary):
                self.work.side_effect = primary
                original_save = compact.save_json

                def save(path, value, **kwargs):
                    if path.name.endswith("-resources.json"):
                        raise OSError("report fsync failed")
                    return original_save(path, value, **kwargs)

                with patch.object(compact, "save_json", side_effect=save):
                    with self.assertRaises(
                        ValueError if primary else OSError
                    ) as caught:
                        compact.run(self.output)
                if primary:
                    self.assertIs(caught.exception, primary)
        self.assertEqual(self.spark.stop.call_count, 2)
        self.assertEqual(self.sampler.close.call_count, 2)

    def test_invalid_input_is_reported_before_starting_spark(self):
        with patch.dict(compact.os.environ, {"TABLE": "invalid; DROP"}):
            with self.assertRaisesRegex(ValueError, "simple namespace"):
                compact.run(self.output)
        self.build.assert_not_called()
        self.assertFalse(self.report()["passed"])


if __name__ == "__main__":
    unittest.main()
