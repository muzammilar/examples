import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import production


class ResourceTests(unittest.TestCase):
    def test_counter_rates_and_generation_reset_are_measured_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = []
            for ns, cpu, io, generation in (
                (1, 100, 20, "old"),
                (3, 500, 60, "old"),
                (4, 900, 80, "new"),
                (6, 1100, 100, "new"),
            ):
                samples.append(
                    {
                        "role": "primary",
                        "sample": {
                            "monotonic_ns": ns * 10**9,
                            "cpu.stat": f"usage_usec {cpu}\nuser_usec {cpu}\nsystem_usec 0\n",
                            "memory.current": "4096",
                            "memory.peak": "8192",
                            "process_started_ticks": generation,
                            "io.stat": f"254:0 rbytes={io} wbytes={io} rios={io} wios={io}\n",
                            "storage": {},
                        },
                    }
                )
            (root / "resources.jsonl").write_text(
                "\n".join(map(json.dumps, samples)) + "\n"
            )
            result = production.resource_summary(root)
            self.assertEqual(result["primary"]["counter_resets_excluded"], 1)
            self.assertAlmostEqual(
                result["primary"]["observed_cpu_core_seconds"], 0.0006
            )
            self.assertEqual(
                result["primary"]["block_io_by_device_counter"]["254:0:wios"],
                {"observed_delta": 60, "observed_seconds": 4, "per_second": 15},
            )
            self.assertIsNone(result["standby"]["observed_cpu_core_seconds"])
            self.assertIsNone(result["standby"]["memory_sampled_max_bytes"])

    def test_bare_device_means_zero_but_missing_device_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = [
                {
                    "role": "controller",
                    "sample": {
                        "monotonic_ns": ns * 10**9,
                        "cpu.stat": "usage_usec 0\nuser_usec 0\nsystem_usec 0\n",
                        "memory.current": "4096",
                        "memory.peak": "8192",
                        "process_started_ticks": "same",
                        "io.stat": "254:0\n",
                        "storage": {},
                    },
                }
                for ns in (1, 2)
            ]
            (root / "resources.jsonl").write_text(
                "\n".join(map(json.dumps, samples)) + "\n"
            )
            result = production.resource_summary(root)
            self.assertEqual(
                result["controller"]["block_io_by_device_counter"]["254:0:rbytes"][
                    "observed_delta"
                ],
                0,
            )
            self.assertNotIn(
                "252:0:rbytes", result["controller"]["block_io_by_device_counter"]
            )


class FixtureTests(unittest.TestCase):
    def test_other_results_base_is_not_skipped_for_current_fixture(self):
        with tempfile.TemporaryDirectory(dir=production.ROOT / "results") as directory:
            base = Path(directory)
            result_root = base / "results"
            result_root.mkdir()
            current = {
                "primary": {"CreatedAt": "today"},
                "standby": {"CreatedAt": "today"},
            }
            (result_root / "fixture-registration-one.json").write_text(
                json.dumps({"volumes": current, "results_base": "results/runA"})
            )
            (result_root / "fixture-registration-old.json").write_text(
                json.dumps({"volumes": {"old": True}, "results_base": "results/old"})
            )
            with (
                patch.object(production, "ROOT", base),
                patch.object(production, "run", return_value=""),
                patch.object(production, "fixture_identity", return_value=current),
                patch.dict(production.os.environ, {"RESULTS_DIR": "results/manual"}),
            ):
                self.assertEqual(
                    production.registered_bases(current), {result_root / "runA"}
                )
                with self.assertRaisesRegex(RuntimeError, "legacy ACK histories"):
                    production.teardown_guard()

    def test_registered_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=production.ROOT / "results") as directory:
            base = Path(directory)
            (base / "results").mkdir()
            (base / "results" / "fixture-registration-escape.json").write_text(
                json.dumps({"volumes": {}, "results_base": "../outside"})
            )
            with patch.object(production, "ROOT", base):
                with self.assertRaisesRegex(RuntimeError, "escape"):
                    production.registered_bases({})


class RetirementTests(unittest.TestCase):
    def test_changed_failed_csv_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=production.ROOT / "results") as directory:
            root = Path(directory)
            archive = root / "archive"
            archive.mkdir()
            complete = archive / "complete.csv"
            complete.write_bytes(b"header\r\ncomplete-body\r\n")
            partial = root / "live_case.csv"
            partial.write_bytes(b"header\r\ncomplete")
            (archive / "result.json").write_text(
                json.dumps({"csv_files": {"case": "complete.csv"}})
            )
            (root / "old-export-partials-preserved.json").write_text(
                json.dumps(
                    {
                        "partial_files": [
                            {"external_path": str(partial.relative_to(production.ROOT))}
                        ]
                    }
                )
            )
            self.assertEqual(
                production.verify_failed_prefixes(root, archive)[0]["bytes"],
                partial.stat().st_size,
            )
            partial.write_bytes(b"header\r\ncorrupted")
            with self.assertRaisesRegex(RuntimeError, "differs from verified"):
                production.verify_failed_prefixes(root, archive)

    def test_changed_failed_suite_evidence_blocks_retirement_replay(self):
        with tempfile.TemporaryDirectory(dir=production.ROOT / "results") as directory:
            suite = Path(directory)
            source = suite / "failure.json"
            source.write_text("original failure")
            value = {
                "status": "failed-but-archived",
                "passed": False,
                "suite": str(suite.relative_to(production.ROOT)),
                "evidence_sha256": {
                    str(source.relative_to(production.ROOT)): production.file_sha256(
                        source
                    )
                },
            }
            (suite / "retirement.json").write_text(json.dumps(value))
            source.write_text("hidden failure")
            with self.assertRaisesRegex(RuntimeError, "failure evidence changed"):
                production.validate_retirement(suite)

    def test_retirement_cannot_dispose_an_earlier_stage_failure(self):
        with tempfile.TemporaryDirectory(
            prefix="full-suite-", dir=production.ROOT / "results"
        ) as directory:
            suite = Path(directory)
            (suite / "failure.json").write_text(
                json.dumps({"steps": [{"target": "disk-test", "exit_code": 1}]})
            )
            with self.assertRaisesRegex(RuntimeError, "diagnosed final export"):
                production.retire_failed_suite(suite)


if __name__ == "__main__":
    unittest.main()
