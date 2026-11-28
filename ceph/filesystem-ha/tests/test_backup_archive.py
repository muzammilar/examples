"""Actual-byte backup gates reject corruption, missing bodies and archive links."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "ceph_backup_control", SCRIPTS / "backup.py"
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "backup.tar"
        self.run = "a" * 32
        self.body = hashlib.shake_256(f"42193:{self.run}:0".encode()).digest(128)
        record = dict(
            run=self.run,
            sequence=0,
            bytes=128,
            path="/objects/" + self.run + "/0",
            sha256=hashlib.sha256(self.body).hexdigest(),
        )
        self.witness = (
            json.dumps(record | {"event": "attempt"})
            + "\n"
            + json.dumps(record | {"event": "ack"})
            + "\n"
        ).encode()

    def archive(self, body=None, link=False, omit=False):
        with tarfile.open(self.path, "w:") as stream:
            for name, data in [("journal.jsonl", self.witness)] + (
                [] if omit else [("bodies/0", self.body if body is None else body)]
            ):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                stream.addfile(entry, io.BytesIO(data))
            if link:
                entry = tarfile.TarInfo("bodies/1")
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/etc/passwd"
                stream.addfile(entry)

    def test_exact_actual_bytes(self):
        self.archive()
        self.assertEqual(
            backup.validate_archive(self.path, self.run, self.witness),
            {"acks": 1, "bytes": 128},
        )

    def test_same_length_corruption_rejected(self):
        self.archive(body=b"x" * 128)
        with self.assertRaisesRegex(RuntimeError, "oracle"):
            backup.validate_archive(self.path, self.run, self.witness)

    def test_missing_ack_body_rejected(self):
        self.archive(omit=True)
        with self.assertRaisesRegex(RuntimeError, "ACK set"):
            backup.validate_archive(self.path, self.run, self.witness)

    def test_archive_link_rejected(self):
        self.archive(link=True)
        with self.assertRaisesRegex(ValueError, "entry or link"):
            backup.validate_archive(self.path, self.run, self.witness)


if __name__ == "__main__":
    unittest.main()
