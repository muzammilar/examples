"""Prevent a synthetic-tail repair from becoming arbitrary log truncation."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from journal_tail import repair_known_tail


class TailRepairGuards(unittest.TestCase):
    prefix = b"known-intact-native-records"

    def prepare(self, root, body=None):
        damaged = root / "damaged"
        damaged.mkdir()
        (damaged / "journal.log").write_bytes(
            body if body is not None else self.prefix + b"\x01\x02\x03"
        )
        (damaged / "journal.qdb").write_bytes(b"opaque-snapshot")
        known = {
            "bytes": len(self.prefix),
            "sha256": hashlib.sha256(self.prefix).hexdigest(),
        }
        return damaged, root / "repair", known

    def test_repair_preserves_damaged_source_and_copies_only_the_known_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            damaged, repaired, known = self.prepare(Path(directory))
            repair_known_tail(damaged, repaired, len(self.prefix), known)
            self.assertEqual((repaired / "journal.log").read_bytes(), self.prefix)
            self.assertEqual(
                (damaged / "journal.log").read_bytes(), self.prefix + b"\x01\x02\x03"
            )
            self.assertEqual(
                (repaired / "journal.qdb").read_bytes(), b"opaque-snapshot"
            )

    def test_unknown_prefix_trailer_or_offset_is_rejected_before_creating_repair(self):
        for body, offset in [
            (self.prefix + b"xyz", len(self.prefix)),
            (b"X" + self.prefix[1:] + b"\x01\x02\x03", len(self.prefix)),
            (self.prefix + b"\x01\x02\x03", len(self.prefix) - 1),
        ]:
            with (
                self.subTest(body=body, offset=offset),
                tempfile.TemporaryDirectory() as directory,
            ):
                damaged, repaired, known = self.prepare(Path(directory), body)
                with self.assertRaises(ValueError):
                    repair_known_tail(damaged, repaired, offset, known)
                self.assertFalse(repaired.exists())
                self.assertEqual((damaged / "journal.log").read_bytes(), body)

    def test_existing_failed_repair_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            damaged, repaired, known = self.prepare(Path(directory))
            repaired.mkdir()
            (repaired / "journal.log").write_bytes(b"keep-this-failed-evidence")
            with self.assertRaises(FileExistsError):
                repair_known_tail(damaged, repaired, len(self.prefix), known)
            self.assertEqual(
                (repaired / "journal.log").read_bytes(), b"keep-this-failed-evidence"
            )


if __name__ == "__main__":
    unittest.main()
