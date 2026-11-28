"""Reject false-success slice mappings and mutated witnesses before live tests."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit import journal, object_key


class Oracle(unittest.TestCase):
    def test_native_slice_key(self):
        entry = {
            "attr": {"length": 262144},
            "chunks": [
                {"index": 0, "slices": [{"id": 1234, "size": 262144, "len": 262144}]}
            ],
        }
        self.assertEqual(object_key(entry), "juicefs-lab/chunks/0/1/1234_0_262144")
        entry["chunks"][0]["slices"][0]["off"] = 1
        with self.assertRaises(ValueError):
            object_key(entry)

    def test_changed_ack_checksum_is_rejected(self):
        run = "a" * 32
        body = hashlib.shake_256(f"96517:{run}:0".encode()).digest(16)
        row = dict(
            run=run, sequence=0, bytes=16, sha256=hashlib.sha256(body).hexdigest()
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "witness.jsonl"
            path.write_text(
                json.dumps(dict(row, event="attempt"))
                + "\n"
                + json.dumps(dict(row, event="ack", sha256="0" * 64))
                + "\n"
            )
            with self.assertRaises(ValueError):
                journal(path, run)

    def test_unresolved_intent_is_rejected(self):
        run = "b" * 32
        row = dict(
            run=run,
            sequence=0,
            bytes=8,
            sha256=hashlib.sha256(
                hashlib.shake_256(f"96517:{run}:0".encode()).digest(8)
            ).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "witness.jsonl"
            path.write_text(json.dumps(dict(row, event="attempt")) + "\n")
            with self.assertRaises(ValueError):
                journal(path, run)
