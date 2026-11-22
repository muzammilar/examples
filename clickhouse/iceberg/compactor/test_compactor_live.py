"""Host-only controls; native Spark/Iceberg execution is a separate qualification."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import compact


class ResultsDirectory(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "results"
        self.root.mkdir()
        self.patch = patch.object(compact, "RESULTS_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_default_and_per_invocation_directory(self):
        invocation = self.root / "maintenance" / "invocation"
        invocation.mkdir(parents=True)
        with patch.dict(compact.os.environ, {}, clear=True):
            self.assertEqual(compact.results_directory(), self.root.resolve())
        with patch.dict(
            compact.os.environ, {"COMPACTION_RESULTS_DIR": str(invocation)}, clear=True
        ):
            self.assertEqual(compact.results_directory(), invocation.resolve())

    def test_outside_relative_and_unnormalized_paths_are_rejected(self):
        for path in (
            str(self.root.parent),
            str(self.root) + "-other",
            "results/job",
            "",
            str(self.root) + "/../results",
            str(self.root) + "/./job",
            str(self.root) + "//job",
            str(self.root) + "/",
        ):
            with self.subTest(path=path):
                with patch.dict(
                    compact.os.environ, {"COMPACTION_RESULTS_DIR": path}, clear=True
                ):
                    with self.assertRaises(ValueError):
                        compact.results_directory()

    def test_missing_directory_file_and_symlink_escape_are_rejected(self):
        file = self.root / "file"
        file.write_text("owned fixture")
        escape = self.root / "escape"
        escape.symlink_to(self.root.parent, target_is_directory=True)
        for path in (self.root / "missing", file, escape):
            with self.subTest(path=path):
                with patch.dict(
                    compact.os.environ,
                    {"COMPACTION_RESULTS_DIR": str(path)},
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        compact.results_directory()
        self.assertFalse((self.root / "missing").exists())

    def test_run_selects_directory_before_any_work(self):
        with patch.dict(
            compact.os.environ, {"COMPACTION_RESULTS_DIR": "/outside"}, clear=True
        ):
            with patch.object(compact, "Sampler") as sampler:
                with self.assertRaises(ValueError):
                    compact.run()
                sampler.assert_not_called()

    def test_run_writes_all_reports_to_the_selected_invocation(self):
        invocation = self.root / "invocation"
        invocation.mkdir()
        spark = Mock()
        spark.sparkContext._jvm.java.lang.management.ManagementFactory.getRuntimeMXBean.return_value.getPid.return_value = 4321
        with patch.dict(
            compact.os.environ, {"COMPACTION_RESULTS_DIR": str(invocation)}, clear=True
        ):
            with patch.object(compact, "Sampler") as sampler:
                sampler.return_value.close.return_value = {}
                with patch.object(compact, "build_spark", return_value=spark):
                    with patch.object(
                        compact,
                        "compact",
                        side_effect=lambda spark, name, target, result: result.update(
                            table=name
                        ),
                    ):
                        report = compact.run()
        self.assertEqual(report.parent, invocation.resolve())
        self.assertEqual(sampler.call_args.args[0].parent, invocation.resolve())
        self.assertTrue((invocation / "lab-probe-compaction.json").is_file())
        self.assertEqual(list(self.root.glob("*.json")), [])


class Configuration(unittest.TestCase):
    def test_defaults_keep_exclusive_writer_contract(self):
        with patch.dict(compact.os.environ, {}, clear=True):
            config = compact.configuration()
        self.assertFalse(config["live_writers"])
        self.assertTrue(config["rewrite_manifests"])
        self.assertEqual(config["s3_endpoint"], "http://storage:8333")
        self.assertEqual(config["http_client_properties"], {})

    def test_flags_require_explicit_boolean_literals(self):
        for flag in ("COMPACTION_LIVE_WRITERS", "REWRITE_MANIFESTS"):
            for value in ("1", "yes", "TRUE", "", "false "):
                with self.subTest(flag=flag, value=value):
                    with patch.dict(compact.os.environ, {flag: value}, clear=True):
                        with self.assertRaisesRegex(ValueError, flag):
                            compact.configuration()
        with patch.dict(
            compact.os.environ,
            {"COMPACTION_LIVE_WRITERS": "true", "REWRITE_MANIFESTS": "false"},
            clear=True,
        ):
            config = compact.configuration()
        self.assertTrue(config["live_writers"])
        self.assertFalse(config["rewrite_manifests"])

    def test_only_exact_owned_s3_endpoints_are_allowed(self):
        for endpoint in ("http://storage:8333", "http://compaction-upload-gate:8333"):
            with patch.dict(
                compact.os.environ, {"COMPACTOR_S3_ENDPOINT": endpoint}, clear=True
            ):
                self.assertEqual(compact.configuration()["s3_endpoint"], endpoint)
        for endpoint in (
            "https://s3.amazonaws.com",
            "http://localhost:8333",
            "http://storage:8333/",
            "http://demo:demo@storage:8333",
            "http://storage:8333?endpoint=other",
            "",
        ):
            with self.subTest(endpoint=endpoint):
                with patch.dict(
                    compact.os.environ, {"COMPACTOR_S3_ENDPOINT": endpoint}, clear=True
                ):
                    with self.assertRaisesRegex(ValueError, "COMPACTOR_S3_ENDPOINT"):
                        compact.configuration()

    def test_catalog_cannot_redirect_local_credentials(self):
        with patch.dict(
            compact.os.environ, {"CATALOG_URL": "http://remote:8181"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "CATALOG_URL"):
                compact.configuration()

    def test_spark_receives_gate_endpoint_with_fixed_local_catalog(self):
        builder = Mock()
        builder.appName.return_value = builder
        builder.config.return_value = builder
        modules = {
            "pyspark": Mock(),
            "pyspark.sql": SimpleNamespace(
                SparkSession=SimpleNamespace(builder=builder)
            ),
        }
        with patch.dict(sys.modules, modules):
            with patch.dict(
                compact.os.environ,
                {"COMPACTOR_S3_ENDPOINT": "http://compaction-upload-gate:8333"},
                clear=True,
            ):
                self.assertIs(compact.build_spark(), builder.getOrCreate.return_value)
        effective = dict(call.args for call in builder.config.call_args_list)
        self.assertEqual(
            effective["spark.sql.catalog.lake.s3.endpoint"],
            "http://compaction-upload-gate:8333",
        )
        self.assertEqual(effective["spark.sql.catalog.lake.uri"], "http://storage:8181")
        self.assertEqual(effective["spark.sql.catalog.lake.s3.access-key-id"], "demo")
        self.assertEqual(effective["spark.sql.catalog.lake.http-client.type"], "apache")
        self.assertEqual(
            effective["spark.sql.catalog.lake.http-client.apache.socket-timeout-ms"],
            "180000",
        )

    def test_normal_storage_builder_keeps_http_defaults(self):
        builder = Mock()
        builder.appName.return_value = builder
        builder.config.return_value = builder
        modules = {
            "pyspark": Mock(),
            "pyspark.sql": SimpleNamespace(
                SparkSession=SimpleNamespace(builder=builder)
            ),
        }
        with patch.dict(sys.modules, modules):
            with patch.dict(compact.os.environ, {}, clear=True):
                compact.build_spark()
        self.assertFalse(
            any(
                ".http-client." in call.args[0]
                for call in builder.config.call_args_list
            )
        )

    def test_invalid_endpoint_is_reported_before_spark_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.dict(
                compact.os.environ,
                {"COMPACTOR_S3_ENDPOINT": "https://s3.amazonaws.com"},
                clear=True,
            ):
                with patch.object(compact, "Sampler") as sampler:
                    sampler.return_value.close.return_value = {}
                    with patch.object(compact, "build_spark") as build:
                        with self.assertRaisesRegex(
                            ValueError, "COMPACTOR_S3_ENDPOINT"
                        ):
                            compact.run(output)
                        build.assert_not_called()
            report = json.loads(next(output.glob("*-resources.json")).read_text())
            self.assertFalse(report["passed"])
            self.assertFalse(report["stop"]["attempted"])


class PinnedMetrics(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(compact.os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.spark, self.table = Mock(), Mock()
        self.loader = self.spark.sparkContext._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable
        self.loader.return_value = self.table
        self.snapshot = self.table.currentSnapshot.return_value
        self.snapshot.snapshotId.return_value = 17
        self.snapshot.manifestListLocation.return_value = (
            "s3://warehouse/lab/probe/metadata/snap-17.avro"
        )
        self.planned = self.table.newScan.return_value.useSnapshot.return_value.planFiles.return_value
        self.iterator = self.planned.iterator.return_value
        self.iterator.hasNext.side_effect = [True, True, False]
        first, second = Mock(), Mock()
        (
            first.location.return_value,
            first.fileSizeInBytes.return_value,
            first.recordCount.return_value,
        ) = "s3://warehouse/a", 100, 2
        (
            second.location.return_value,
            second.fileSizeInBytes.return_value,
            second.recordCount.return_value,
        ) = "s3://warehouse/b", 200, 3
        self.iterator.next.side_effect = [
            SimpleNamespace(file=lambda: first),
            SimpleNamespace(file=lambda: second),
        ]
        self.spark.sql.return_value.first.return_value = (5,)

    def test_gate_checks_and_records_effective_nonsecret_properties(self):
        expected = {
            "http-client.type": "apache",
            "http-client.apache.socket-timeout-ms": "180000",
        }
        self.table.io.return_value.properties.return_value.get.side_effect = (
            expected.get
        )
        observed = {}
        with patch.dict(
            compact.os.environ,
            {"COMPACTOR_S3_ENDPOINT": "http://compaction-upload-gate:8333"},
        ):
            result = compact.metrics(self.spark, "lab.probe", effective_http=observed)
        self.assertEqual(observed, expected)
        self.assertEqual(result["http_client_properties"], expected)
        self.assertEqual(
            [
                call.args[0]
                for call in self.table.io.return_value.properties.return_value.get.call_args_list
            ],
            list(expected),
        )

    def test_table_override_rejected_before_refresh_scan_or_sql(self):
        for overrides in (
            {
                "http-client.type": "apache",
                "http-client.apache.socket-timeout-ms": "30000",
            },
            {
                "http-client.type": "urlconnection",
                "http-client.apache.socket-timeout-ms": "180000",
            },
            {"http-client.type": "apache"},
        ):
            with self.subTest(overrides=overrides):
                self.table.io.return_value.properties.return_value.get.side_effect = (
                    overrides.get
                )
                observed = {}
                with patch.dict(
                    compact.os.environ,
                    {"COMPACTOR_S3_ENDPOINT": "http://compaction-upload-gate:8333"},
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "effective gated S3 HTTP"
                    ):
                        compact.metrics(
                            self.spark, "lab.probe", effective_http=observed
                        )
                self.assertEqual(
                    observed["http-client.type"], overrides["http-client.type"]
                )
        self.table.refresh.assert_not_called()
        self.table.newScan.assert_not_called()
        self.spark.sql.assert_not_called()

    def test_normal_storage_does_not_claim_measured_http_settings(self):
        result = compact.metrics(self.spark, "lab.probe")
        self.assertIsNone(result["http_client_properties"])
        self.table.io.assert_not_called()

    def test_override_keeps_diagnostic_and_cannot_submit_rewrite(self):
        overrides = {
            "http-client.type": "apache",
            "http-client.apache.socket-timeout-ms": "30000",
        }
        self.table.io.return_value.properties.return_value.get.side_effect = (
            overrides.get
        )
        self.spark.version = "4.0.1"
        result = {}
        with patch.dict(
            compact.os.environ,
            {"COMPACTOR_S3_ENDPOINT": "http://compaction-upload-gate:8333"},
        ):
            with self.assertRaisesRegex(RuntimeError, "effective gated S3 HTTP"):
                compact.compact(self.spark, "lab.probe", 1048576, result)
        self.assertEqual(result["effective_http_client_properties"], overrides)
        self.assertEqual(
            result["configuration"]["http_client_properties"][
                "http-client.apache.socket-timeout-ms"
            ],
            "180000",
        )
        self.spark.sql.assert_not_called()

    def test_head_change_cannot_mix_snapshot_files_and_rows(self):
        # Advance the mocked head while file planning is in progress. Every read
        # must still bind to 17, rather than consulting the newer current head.
        def advance_head():
            self.snapshot.snapshotId.return_value = 99
            return self.iterator

        self.planned.iterator.side_effect = advance_head
        result = compact.metrics(self.spark, "lab.probe")
        self.loader.assert_called_once_with(self.spark._jsparkSession, "lake.lab.probe")
        self.table.refresh.assert_called_once()
        self.table.currentSnapshot.assert_called_once()
        self.table.newScan.return_value.useSnapshot.assert_called_once_with(17)
        self.spark.sql.assert_called_once_with(
            "SELECT count(*) FROM lake.lab.probe VERSION AS OF 17"
        )
        self.assertEqual(
            (result["snapshot"], result["files"], result["rows"], result["bytes"]),
            (17, 2, 5, 300),
        )
        self.assertEqual((result["min_bytes"], result["max_bytes"]), (100, 200))
        self.assertEqual(sum(item["records"] for item in result["file_membership"]), 5)
        self.iterator.close.assert_called_once()
        self.planned.close.assert_called_once()

    def test_empty_snapshot_membership_still_counts_exact_snapshot(self):
        self.iterator.hasNext.side_effect = [False]
        self.spark.sql.return_value.first.return_value = (0,)
        result = compact.metrics(self.spark, "lab.probe")
        self.assertEqual((result["files"], result["bytes"], result["rows"]), (0, 0, 0))
        self.assertEqual((result["min_bytes"], result["max_bytes"]), (0, 0))

    def test_uncommitted_table_is_not_accepted_as_empty(self):
        self.table.currentSnapshot.return_value = None
        with self.assertRaisesRegex(RuntimeError, "committed snapshot"):
            compact.metrics(self.spark, "lab.probe")
        self.table.newScan.assert_not_called()
        self.spark.sql.assert_not_called()

    def test_scan_error_survives_both_cleanup_errors(self):
        original = RuntimeError("manifest read failed")
        self.iterator.next.side_effect = original
        self.iterator.close.side_effect = OSError("iterator close failed")
        self.planned.close.side_effect = OSError("plan close failed")
        with self.assertRaises(RuntimeError) as caught:
            compact.metrics(self.spark, "lab.probe")
        self.assertIs(caught.exception, original)
        self.iterator.close.assert_called_once()
        self.planned.close.assert_called_once()
        self.spark.sql.assert_not_called()

    def test_cleanup_failure_after_successful_scan_is_fatal(self):
        self.iterator.close.side_effect = OSError("iterator close failed")
        with self.assertRaisesRegex(OSError, "iterator close failed"):
            compact.metrics(self.spark, "lab.probe")
        self.planned.close.assert_called_once()

    def test_foreign_bucket_fails_and_closes_scan(self):
        data = Mock()
        data.location.return_value = "s3://foreign/a"
        self.iterator.next.side_effect = [SimpleNamespace(file=lambda: data)]
        with self.assertRaisesRegex(ValueError, "outside the lab warehouse"):
            compact.metrics(self.spark, "lab.probe")
        self.iterator.close.assert_called_once()
        self.planned.close.assert_called_once()


class LiveRowContract(unittest.TestCase):
    def execute(self, after=10, retained=10, live=False, manifests=True):
        spark, result = Mock(version="4.0.1"), {}
        spark.sql.return_value.collect.return_value = []
        env = {
            "COMPACTION_LIVE_WRITERS": str(live).lower(),
            "REWRITE_MANIFESTS": str(manifests).lower(),
        }
        with patch.dict(compact.os.environ, env, clear=True):
            with patch.object(
                compact,
                "metrics",
                side_effect=[
                    {"snapshot": 17, "rows": 10},
                    {"snapshot": 18, "rows": after},
                ],
            ):
                with patch.object(
                    compact, "snapshot_rows", return_value=retained
                ) as old_count:
                    compact.compact(spark, "lab.probe", 1048576, result)
        old_count.assert_called_once_with(spark, "lab.probe", 17)
        return spark, result

    def test_exclusive_writer_requires_equal_row_count(self):
        self.execute()
        with self.assertRaisesRegex(RuntimeError, "declared writer mode"):
            self.execute(after=11)

    def test_live_appends_allow_growth_and_unchanged_counts(self):
        for after in (10, 11):
            _, result = self.execute(after=after, live=True)
            self.assertEqual(result["retained_snapshot_rows"], 10)
            self.assertEqual(
                result["row_count_check"]["mode"], "nondecreasing-with-live-appends"
            )

    def test_live_writers_do_not_allow_row_loss(self):
        with self.assertRaisesRegex(RuntimeError, "declared writer mode"):
            self.execute(after=9, live=True)

    def test_retained_snapshot_must_remain_exact_even_with_live_growth(self):
        for retained in (9, 11):
            with self.assertRaisesRegex(RuntimeError, "retained snapshot changed"):
                self.execute(after=12, retained=retained, live=True)

    def test_single_atomic_rewrite_and_separate_manifest_toggle(self):
        for manifests in (False, True):
            spark, result = self.execute(live=True, manifests=manifests)
            queries = [call.args[0] for call in spark.sql.call_args_list]
            self.assertIn("'partial-progress.enabled','false'", queries[0])
            self.assertIn("'max-concurrent-file-group-rewrites','1'", queries[0])
            self.assertEqual(
                any("rewrite_manifests" in query for query in queries), manifests
            )
            self.assertEqual(result["manifest_rewrite_seconds"] is not None, manifests)
            self.assertEqual(result["manifests"], [])
            self.assertGreaterEqual(result["data_rewrite_seconds"], 0)

    def test_rewrite_error_preserves_before_snapshot_and_never_checks_new_head(self):
        spark, result = Mock(version="4.0.1"), {}
        original = RuntimeError("upload interrupted")
        spark.sql.side_effect = original
        with patch.dict(compact.os.environ, {}, clear=True):
            with patch.object(
                compact, "metrics", return_value={"snapshot": 17, "rows": 10}
            ) as metrics:
                with self.assertRaises(RuntimeError) as caught:
                    compact.compact(spark, "lab.probe", 1048576, result)
        self.assertIs(caught.exception, original)
        self.assertEqual(result["before"]["snapshot"], 17)
        self.assertNotIn("after", result)
        metrics.assert_called_once()


if __name__ == "__main__":
    unittest.main()
