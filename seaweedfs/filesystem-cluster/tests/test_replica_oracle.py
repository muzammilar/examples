"""Corruption/missing-ACK controls for the independent native-copy verifier."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_audit


class ReplicaOracle(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        (root / ".build").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root / ".build")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run = "a" * 32
        (self.root / "journals").mkdir()
        (self.root / "fixture.json").write_text(json.dumps({"runs": [self.run]}))
        self.body = hashlib.shake_256(f"95513:{self.run}:0".encode()).digest(13)
        self.row = {
            "run": self.run,
            "sequence": 0,
            "bytes": 13,
            "sha256": hashlib.sha256(self.body).hexdigest(),
        }
        self.journal = self.root / "journals" / (self.run + ".jsonl")
        self.journal.write_text(
            "".join(
                json.dumps(self.row | {"event": event}) + "\n"
                for event in ["attempt", "ack"]
            )
        )
        self.corrupt = False
        self.missing = False

    def get(self, url):
        if "/status?" in url:
            return json.dumps({"Volumes": [{"Id": 1}]}).encode()
        if "?metadata=true" in url:
            if self.missing:
                raise HTTPError(url, 404, "missing", None, None)
            return json.dumps(
                {"FileSize": 13, "chunks": [{"file_id": "1,abcdef", "size": 13}]}
            ).encode()
        return b"X" * 13 if self.corrupt and "volume1:" in url else self.body

    def run_audit(self):
        def path(value):
            return self.root / Path(value).relative_to("/results")

        out = io.StringIO()
        with patch.object(native_audit, "Path", path), patch.object(
            native_audit, "get", self.get
        ), redirect_stdout(out):
            native_audit.main()
        return json.loads(out.getvalue())

    def test_all_three_bodies_are_checked(self):
        result = self.run_audit()
        self.assertEqual(
            (
                result["acks"],
                result["direct_replica_reads"],
                result["physical_body_bytes"],
            ),
            (1, 3, 39),
        )

    def test_one_same_length_corrupt_replica_fails(self):
        self.corrupt = True
        with self.assertRaisesRegex(RuntimeError, "Native replica body mismatch"):
            self.run_audit()

    def test_missing_ack_is_not_an_optional_absence(self):
        self.missing = True
        with self.assertRaises(HTTPError):
            self.run_audit()

    def test_truncated_intent_is_rejected(self):
        self.journal.write_text(json.dumps(self.row | {"event": "attempt"}))
        with self.assertRaisesRegex(RuntimeError, "Incomplete external journal"):
            self.run_audit()

    def test_wrong_witness_hash_is_rejected_before_readback(self):
        self.journal.write_text(
            json.dumps(self.row | {"event": "attempt", "sha256": "0" * 64}) + "\n"
        )
        with self.assertRaisesRegex(RuntimeError, "independent payload oracle"):
            self.run_audit()


if __name__ == "__main__":
    unittest.main()
