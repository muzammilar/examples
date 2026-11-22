"""Keep a healthy round trip from concealing bad cold data or a bypass route."""

import json
from pathlib import Path
import tempfile
import unittest

import duckdb_clickhouse as connector
import duckdb_partial as partial


class ConnectorRoundTrip(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
