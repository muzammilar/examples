"""Negative controls for the independent witness gate; no cluster required."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location(
    "cephfs_checks", Path(sys.path[0]) / "test.py"
)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class History(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "journal.jsonl"
        self.run = "a" * 32
        self.row = {
            "run": self.run,
            "sequence": 0,
            "path": "/objects/" + self.run + "/0",
            "bytes": 13,
            "sha256": hashlib.sha256(
                hashlib.shake_256(("42193:" + self.run + ":0").encode()).digest(13)
            ).hexdigest(),
        }

    def write(self, rows):
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_ack_and_incomplete_intent_stay_distinct(self):
        self.write([self.row | {"event": "attempt"}])
        self.assertEqual(
            checks.history(self.path, self.run)["ambiguous_or_incomplete"], 1
        )
        self.write([self.row | {"event": "attempt"}, self.row | {"event": "ack"}])
        self.assertEqual(checks.history(self.path, self.run)["acknowledged_bytes"], 13)

    def test_matching_but_wrong_payload_hash_is_rejected(self):
        bad = self.row | {"sha256": "0" * 64}
        self.write([bad | {"event": "attempt"}, bad | {"event": "ack"}])
        with self.assertRaisesRegex(RuntimeError, "independent"):
            checks.history(self.path, self.run)

    def test_ack_without_intent_is_rejected(self):
        self.write([self.row | {"event": "ack"}])
        with self.assertRaises(ValueError):
            checks.history(self.path, self.run)

    def test_duplicate_ack_is_rejected(self):
        self.write(
            [self.row | {"event": "attempt"}] + [self.row | {"event": "ack"}] * 2
        )
        with self.assertRaises(ValueError):
            checks.history(self.path, self.run)

    def test_truncated_history_is_rejected(self):
        self.path.write_text(json.dumps(self.row | {"event": "attempt"}))
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            checks.history(self.path, self.run)

    def test_foreign_path_is_rejected(self):
        self.write([self.row | {"event": "attempt", "path": "/objects/foreign/0"}])
        with self.assertRaises(ValueError):
            checks.history(self.path, self.run)


if __name__ == "__main__":
    unittest.main()
