"""Guard the external oracle against silent truncation or acknowledgement drift."""

import tempfile
import unittest
from pathlib import Path

from witness import attempted_objects, records


class Witness(unittest.TestCase):
    def test_history_includes_unresolved_attempt(self):
        values = attempted_objects(
            [
                {"stage": "attempt", "object": {"key": "one"}},
                {"stage": "ack", "key": "one"},
                {"stage": "attempt", "object": {"key": "two"}},
            ]
        )
        self.assertEqual([v["acked"] for v in values], [True, False])

    def test_duplicate_or_orphan_outcome_fails(self):
        for events in [
            [{"stage": "ack", "key": "one"}],
            [{"stage": "attempt", "object": {"key": "one"}}] * 2,
            [
                {"stage": "attempt", "object": {"key": "one"}},
                {"stage": "ack", "key": "one"},
                {"stage": "ack", "key": "one"},
            ],
        ]:
            with self.assertRaises(RuntimeError):
                attempted_objects(events)

    def test_truncated_journal_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer.jsonl"
            path.write_text('{"stage":"ack"}')
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                records(path)
