"""Keep a healthy round trip from concealing bad cold data or a bypass route."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import os

import duckdb_clickhouse as connector
import duckdb_partial as partial
import duckdb_crash as crash
import duckdb_dependencies as dependencies
import connector_compactor_probe as compactor_probe
import connector_concurrent as concurrent
import connector_read_isolation as isolation
import duckdb_http as http


class ConnectorRoundTrip(unittest.TestCase):
    def test_http_errors_or_missing_responses_cannot_pass_from_good_body_counts(self):
        for status, body, missing in [
            (403, "denied", False),
            (200, "Code: 241", False),
            (200, "", True),
        ]:
            with (
                self.subTest(status=status, body=body, missing=missing),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                row = self.fixture(root)
                (root / "invocation.json").write_text(
                    json.dumps({"mode": "connector-http-hot"})
                )
                (root / "loaded-extensions.json").write_text(
                    json.dumps(
                        [
                            {
                                "extension_name": "http_client",
                                "extension_version": "9bf6584a9d",
                            },
                            {
                                "extension_name": "httpfs",
                                "extension_version": "f71cfdb4bf",
                            },
                        ]
                    )
                )
                (root / "create-response.json").write_text(
                    json.dumps([{"value": {"status": 200, "body": ""}}])
                )
                if not missing:
                    (root / "insert-0-response.json").write_text(
                        json.dumps([{"value": {"status": status, "body": body}}])
                    )
                (root / "hot-rows.json").write_text(json.dumps([row]))
                with self.assertRaisesRegex(RuntimeError, "HTTP (mutation|response)"):
                    http.verify(root, 10000)

    def test_http_application_sql_does_not_load_the_legacy_scanner(self):
        sql = http.artifacts("duck_compact_" + "a" * 32, 2000000, hot_only=True)[
            "run.sql"
        ]
        self.assertNotIn("clickhouse_scanner", sql)
        self.assertNotIn("INSERT INTO lake.", sql)
        for value in ("http_retries=0", "JSONColumns", "read_json(", "http_post("):
            self.assertIn(value, sql)
        with self.assertRaises(ValueError):
            http.artifacts("duck_compact_existing;DROP TABLE x", 2000000)

    def test_wrong_ingress_flags_fail_even_when_recomputed_bodies_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            row = self.fixture(root)
            for threads in (1, 2):
                for stage in ("local", "stream", "stored"):
                    (root / f"{stage}-{threads}.json").write_text(json.dumps([row]))
                (root / f"flags-{threads}.json").write_text(
                    json.dumps(
                        [
                            {
                                "rows": 10000,
                                "ingress_bad_rows": 1 if threads == 2 else 0,
                                "null_flags": 0,
                            }
                        ]
                    )
                )
            with self.assertRaisesRegex(RuntimeError, "flags-2"):
                isolation.verify_read(root, 10000)

    def test_materialized_success_does_not_hide_stream_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            row = self.fixture(root)
            for threads in (1, 2):
                for stage in ("local", "stream", "stored"):
                    value = row | {"invalid_bodies": 42} if stage == "stream" else row
                    (root / f"{stage}-{threads}.json").write_text(json.dumps([value]))
                (root / f"flags-{threads}.json").write_text(
                    json.dumps(
                        [{"rows": 10000, "ingress_bad_rows": 0, "null_flags": 0}]
                    )
                )
            with self.assertRaisesRegex(RuntimeError, "stream-1, stream-2"):
                isolation.verify_read(root, 10000)
            self.assertEqual(
                json.loads((root / "isolation-checks.json").read_text())[
                    "failed_stages"
                ],
                ["stream-1", "stream-2"],
            )

    def test_parallel_wrong_answer_fails_even_when_the_serial_control_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            row = self.fixture(root)
            (root / "threads-1.json").write_text(json.dumps([row]))
            (root / "threads-2.json").write_text(
                json.dumps([row | {"invalid_bodies": 1}])
            )
            with self.assertRaisesRegex(RuntimeError, "wrong-answer regression: 2"):
                connector.verify_read(root, 10000)

    def test_identifier_and_count_limits_apply_before_any_runtime_action(self):
        for table, rows, customers, batch in [
            ("existing_table", 2000000, 10000, 100000),
            ("duck_frontend_" + "a" * 32, 10000001, 10000, 100000),
            ("duck_frontend_" + "a" * 32, 2000000, 0, 100000),
            ("duck_frontend_" + "a" * 32, 2000000, 10000, 1),
        ]:
            with self.subTest(table=table), self.assertRaises(ValueError):
                connector.artifacts(table, rows, customers, batch)

    def fixture(self, root):
        row = {
            "rows": 10000,
            "unique_ids": 10000,
            "first_id": 0,
            "last_id": 9999,
            "amount_total_cents": 50005000,
            "invalid_bodies": 0,
        }
        (root / "native-exit.txt").write_text("0\n")
        for stage in ("hot", "cold", "restored"):
            (root / (stage + "-rows.json")).write_text(json.dumps([row]))
        return row

    def test_wrong_cold_body_fails_even_with_matching_counts_and_amounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            row = self.fixture(root)
            (root / "cold-rows.json").write_text(
                json.dumps([row | {"invalid_bodies": 1}])
            )
            with self.assertRaisesRegex(
                RuntimeError, "complete deterministic body oracle"
            ):
                connector.verify(root, 10000)

    def test_direct_s3_extension_cannot_qualify_the_clickhouse_connector_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.fixture(root)
            (root / "connector.json").write_text(
                json.dumps(
                    [
                        {
                            "engine": "v2.0.1",
                            "extension_version": connector.REVISION,
                            "loaded": True,
                            "unsigned_allowed": False,
                        }
                    ]
                )
            )
            (root / "loaded-extensions.json").write_text(
                json.dumps([{"extension_name": "httpfs", "loaded": True}])
            )
            with self.assertRaisesRegex(RuntimeError, "bypassed ClickHouse"):
                connector.verify(root, 10000)


class PartialInsertControls(unittest.TestCase):
    def test_no_partial_effects_is_not_a_reproduction(self):
        with self.assertRaisesRegex(RuntimeError, "partial insert"):
            partial.check_effects(
                {"rows": 0, "unique_ids": 0, "wrong_values": 0}, {}, {}, {}, 100
            )

    def test_wrong_values_fail_even_if_replay_count_matches(self):
        first = {"rows": 90, "unique_ids": 90, "wrong_values": 0}
        second = first | {"rows": 180}
        final = {
            "rows": 181,
            "unique_ids": 91,
            "wrong_values": 0,
            "recovery_markers": 1,
        }
        with self.assertRaisesRegex(RuntimeError, "replay did not reconcile"):
            partial.check_effects(
                first,
                second,
                final,
                {"rows": 100, "unique_ids": 100, "wrong_values": 1},
                100,
            )

    def test_native_final_disagreement_is_not_ignored(self):
        first = {"rows": 90, "unique_ids": 90, "wrong_values": 0}
        with self.assertRaisesRegex(RuntimeError, "native final"):
            partial.check_effects(first, first | {"rows": 180}, {"rows": 0}, {}, 100)


class LiveCrashReconciliation(unittest.TestCase):
    def group(self, **changes):
        return {
            "batch": 0,
            "rows": 10000,
            "unique_ids": 10000,
            "first_id": 0,
            "last_id": 9999,
            "invalid_bodies": 0,
        } | changes

    def test_acknowledged_missing_batch_fails(self):
        with self.assertRaisesRegex(RuntimeError, "acknowledged batch missing"):
            crash.reconcile([], [{"first": 0, "end": 10000, "status": "ok"}])

    def test_partial_ambiguous_request_is_not_acknowledged(self):
        result = crash.reconcile(
            [self.group(rows=5000, unique_ids=5000, last_id=4999)],
            [{"first": 0, "end": 10000, "status": "ambiguous"}],
        )
        self.assertEqual(result["acknowledged_rows_verified"], 0)
        self.assertEqual(result["unacknowledged_applied_rows"], 5000)

    def test_matching_totals_do_not_hide_bad_bodies(self):
        with self.assertRaisesRegex(RuntimeError, "corrupt"):
            crash.reconcile(
                [self.group(invalid_bodies=1)],
                [{"first": 0, "end": 10000, "status": "ok"}],
            )

    def test_rows_without_intents_fail(self):
        with self.assertRaisesRegex(RuntimeError, "outside the external intent"):
            crash.reconcile([self.group()], [])


class DependencyReadControls(unittest.TestCase):
    def test_correct_counts_do_not_hide_wrong_archive_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "cold-reader-000000-stdout.json").write_text(
                json.dumps(
                    [{"rows": 2000000, "unique_ids": 2000000, "invalid_bodies": 1}]
                )
            )
            with self.assertRaisesRegex(RuntimeError, "complete-body"):
                dependencies.checked_cold(
                    root, {"sequence": 0, "status": "ok"}, 2000000
                )

    def test_connection_failure_is_not_a_recovered_read(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "did not recover"):
                dependencies.checked_cold(
                    Path(directory), {"sequence": 0, "status": "fail"}, 2000000
                )


class ReadOnlySourceReuse(unittest.TestCase):
    def test_a_faulted_fixture_cannot_bypass_the_fresh_copy_capacity_gate(self):
        identifier = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = root / "duckdb-connector-dependencies" / identifier
            case.mkdir(parents=True)
            (case / "invocation.json").write_text(
                json.dumps(
                    {
                        "mode": "connector-dependencies",
                        "rows": 2000000,
                        "table": "duck_compact_" + identifier.replace("-", ""),
                    }
                )
            )
            (case / "fault-8333-intent.json").write_text("{}")
            with (
                mock.patch.dict(os.environ, {"COLD_SOURCE_CASE": identifier}),
                mock.patch.object(
                    dependencies.cleanup, "results_root", return_value=root
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "pre-fault source"):
                    dependencies.seed_case(2000000)


class CompactorSourceControls(unittest.TestCase):
    def test_noncanonical_source_is_rejected_before_any_backend_access(self):
        with self.assertRaises(ValueError):
            compactor_probe.source("../some-other-example")

    def test_snapshot_and_row_ids_cannot_be_injected_into_native_sql(self):
        identifier = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = root / "duckdb-connector-dependencies" / identifier
            case.mkdir(parents=True)
            for name, value in {
                "result.json": {
                    "passed": True,
                    "checks": {"recovery_probe_rows_verified": 3000},
                },
                "invocation.json": {
                    "table": "duck_compact_" + identifier.replace("-", "")
                },
                "probe-catalog-final.json": {
                    "metadata": {
                        "table-uuid": identifier,
                        "current-snapshot-id": "1; DROP TABLE",
                    }
                },
                "cold-input.json": {"append_first": 2000000},
            }.items():
                (case / name).write_text(json.dumps(value))
            with mock.patch.object(
                compactor_probe.cleanup, "results_root", return_value=root
            ):
                with self.assertRaisesRegex(RuntimeError, "bounded native IDs"):
                    compactor_probe.source(identifier)


class ConcurrentControls(unittest.TestCase):
    def test_other_visible_rows_cannot_mask_a_missing_acknowledged_batch(self):
        value = {
            "rows": 2002000,
            "unique_ids": 2002000,
            "invalid_bodies": 0,
            "partial_batches": 0,
            "acknowledged_rows": 2000000,
        }
        with self.assertRaisesRegex(RuntimeError, "missing"):
            concurrent.check_read(value, 2001000, 2100000)
        self.assertIn(
            "batch IN (2000)", concurrent.read_oracle("owned", 2100000, 2000000, [2000])
        )

    def test_startup_acks_do_not_establish_native_rewrite_overlap(self):
        native = {"rewrite_started_ns": 1000, "rewrite_ended_ns": 2000}
        clocks = [{"offset_low": -10, "offset_high": 10}]
        events = [
            {"status": "ok", "started_ns": 500, "ended_ns": 800},
            {"status": "ok", "started_ns": 1200, "ended_ns": 1300},
        ]
        start, end, matched = concurrent.call_overlap(native, clocks, events)
        self.assertEqual((start, end), (1010, 1990))
        self.assertEqual(matched, events[1:])

    def test_cold_batches_reconcile_in_their_actual_one_thousand_row_units(self):
        group = {
            "batch": 0,
            "rows": 1000,
            "unique_ids": 1000,
            "first_id": 0,
            "last_id": 999,
            "invalid_bodies": 0,
        }
        proof = crash.reconcile(
            [group], [{"first": 0, "end": 1000, "status": "ok"}], batch_size=1000
        )
        self.assertEqual(proof["acknowledged_rows_verified"], 1000)


class ConcurrentFinalization(unittest.TestCase):
    def test_late_worker_error_cannot_be_hidden_by_successful_final_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "cold-actor-errors.json").write_text(
                json.dumps(["reader failed after metadata audit started"])
            )
            with self.assertRaisesRegex(RuntimeError, "late cold actor"):
                concurrent.audit_journals(root)

    def test_a_successful_rpc_without_its_body_proof_fails_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            archive = {"sequence": 0, "status": "ok", "native_client_fenced": True}
            (root / "cold-history.json").write_text(json.dumps([archive]))
            (root / "concurrent-input.json").write_text(
                json.dumps({"seed_rows": 2000000})
            )
            for role in ("archive", "cold-reader"):
                (root / (role + "-000000-intent.json")).write_text("{}")
                (root / (role + "-000000-result.json")).write_text(json.dumps(archive))
            with self.assertRaisesRegex(RuntimeError, "no body proof"):
                concurrent.audit_journals(root)


class MaintenanceAvailability(unittest.TestCase):
    def test_fast_memory_failures_do_not_disappear_from_the_denominator(self):
        history = [{"role": "cold-reader", "status": "ok"}] * 19 + [
            {"role": "cold-reader", "status": "fail"}
        ] * 32
        rates = concurrent.first_attempt_rates(history)["cold-reader"]
        self.assertEqual(rates["attempted"], 51)
        self.assertEqual(rates["failed_or_ambiguous"], 32)
        self.assertAlmostEqual(rates["success_rate"], 19 / 51)
        self.assertLess(rates["success_rate"], 0.95)


if __name__ == "__main__":
    unittest.main()
