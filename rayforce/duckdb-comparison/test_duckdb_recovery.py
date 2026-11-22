"""Reject weak progress evidence and incomplete/unsafe exact-row predicates."""

import unittest

from duckdb_recovery import exact_row_query, require_progress


class RecoveryControls(unittest.TestCase):
    def test_exact_body_predicate_includes_every_field(self):
        self.assertEqual(
            exact_row_query([0, 2, 3, 4, 5, 6]),
            "SELECT count(*) FROM trades WHERE id=0 AND customer=2 AND symbol=3 AND day=4 AND qty=5 AND price=6",
        )
        for row in ([1], [1, 2, 3, 4, 5, "6 OR true"], [1, 2, 3, 4, 5, True]):
            with self.subTest(row=row), self.assertRaises(ValueError):
                exact_row_query(row)

    def test_writer_only_failures_and_prior_phase_progress_are_insufficient(self):
        writes = [{"kind": "write", "phase": "baseline", "status": "ok"}]
        for events in (
            writes,
            writes + [{"kind": "read", "phase": "baseline", "status": "error"}],
            writes + [{"kind": "read", "phase": "other", "status": "ok"}],
        ):
            with self.subTest(events=events), self.assertRaises(RuntimeError):
                require_progress(events, "baseline")
        require_progress(
            writes + [{"kind": "read", "phase": "baseline", "status": "ok"}], "baseline"
        )


if __name__ == "__main__":
    unittest.main()
