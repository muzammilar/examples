"""Guard the native maintenance scope and the complete-body oracle."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import duckdb_maintenance as maintenance


class NativeMaintenance(unittest.TestCase):
    def test_fencing_failure_does_not_skip_resource_shutdown(self):
        planned = Mock(side_effect=OSError("runtime unavailable"))
        sampler = Mock()
        sampler.close.return_value = ([], {})
        with patch.object(maintenance.control, "save") as save:
            errors = maintenance.finish_cli(Path("unused"), planned, sampler, [])
        sampler.close.assert_called_once()
        save.assert_called_once()
        self.assertEqual(len(errors), 1)
        self.assertIn("fencing", errors[0])

    def test_all_cleanup_failures_are_retained_and_survivors_still_checked(self):
        planned = Mock(side_effect=OSError("inspection failed"))
        sampler = Mock()
        sampler.close.side_effect = RuntimeError("sampler failed")
        with patch.object(
            maintenance.control, "owned", side_effect=OSError("survivor unknown")
        ) as owned:
            errors = maintenance.finish_cli(
                Path("unused"), planned, sampler, [{"Id": "backend"}]
            )
        self.assertEqual(len(errors), 3)
        sampler.close.assert_called_once()
        owned.assert_called_once_with("backend")
        self.assertIn("resources", errors[1])
        self.assertIn("survivor", errors[2])

    def test_parquet_case_does_not_hide_deleted_or_duplicated_files(self):
        entry = {
            "status": "ADDED",
            "content": "DATA",
            "manifest_content": "DATA",
            "file_format": "parquet",
            "file_path": "s3://warehouse/lab/data/a.parquet",
        }
        self.assertEqual(maintenance.live_files([entry]), [entry])
        self.assertEqual(
            len(maintenance.live_files([entry | {"file_format": "PARQUET"}])), 1
        )
        self.assertEqual(
            maintenance.live_files([entry, entry | {"status": "DELETED"}]), [entry]
        )
        with self.assertRaises(RuntimeError):
            maintenance.live_files([entry, entry])
        for bad in (
            {"status": "UNKNOWN"},
            {"content": "POSITION_DELETES"},
            {"file_format": "ORC"},
        ):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                maintenance.live_files([entry | bad])

    def test_wrong_body_is_rejected_even_when_counts_and_sums_match(self):
        value = {
            "rows": 10000,
            "unique_ids": 10000,
            "first_id": 0,
            "last_id": 9999,
            "amount_total_cents": 50005000,
            "invalid_bodies": 0,
        }
        maintenance.check_rows(value, 10000)
        with self.assertRaises(RuntimeError):
            maintenance.check_rows(value | {"invalid_bodies": 1}, 10000)

    def test_duplicate_or_missing_identity_cannot_hide_behind_valid_body_flags(self):
        value = {
            "rows": 10000,
            "unique_ids": 9999,
            "first_id": 0,
            "last_id": 9999,
            "amount_total_cents": 50005000,
            "invalid_bodies": 0,
        }
        with self.assertRaises(RuntimeError):
            maintenance.check_rows(value, 10000)

    def test_non_owned_identifiers_and_unbounded_counts_fail_before_runtime(self):
        for table, rows, customers, batch in [
            ("events", 2000000, 10000, 100000),
            (
                "duck_compact_" + "a" * 32 + "; DROP SCHEMA lake.lab",
                2000000,
                10000,
                100000,
            ),
            ("duck_compact_" + "a" * 32, 2000000, 0, 100000),
            ("duck_compact_" + "a" * 32, 2000000, 10000, 2000000),
            ("duck_compact_" + "a" * 32, 10000001, 10000, 100000),
        ]:
            with self.subTest(table=table, rows=rows), self.assertRaises(ValueError):
                maintenance.program(table, rows, customers, batch)


if __name__ == "__main__":
    unittest.main()
