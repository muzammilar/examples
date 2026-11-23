"""Guard corruption fixtures and prove the external oracle rejects loss/wrong bodies."""

import tempfile
import hashlib
import json
from pathlib import Path
import unittest
import zlib

from aof import COMMIT_LENGTH, COMMIT_MAGIC, HEADER, faults, layout, payload, scan_proof


def fixture():
    data = bytearray()
    for identity in range(300):
        body = payload(identity, 64)
        length = HEADER.pack(len(body), 0)[:4]
        data += HEADER.pack(len(body), zlib.crc32(length + body)) + body
        if (identity + 1) % 100 == 0:
            data += HEADER.pack(COMMIT_LENGTH, COMMIT_MAGIC)
    return bytes(data)


class AofGuards(unittest.TestCase):
    def test_fixture_requires_correct_crcs_and_complete_committed_records(self):
        data = fixture()
        records, commits = layout(data)
        self.assertEqual(len(records), 300)
        self.assertEqual([r["committed_records"] for r in commits], [100, 200, 300])
        for bad in (data[:-1], data[:-8], b"wrong", bytes([data[0] ^ 1]) + data[1:]):
            with self.subTest(bytes=len(bad)), self.assertRaises(ValueError):
                layout(bad)

    def test_faults_change_only_the_declared_single_bit_or_known_trailer(self):
        data = fixture()
        for name, (changed, detail) in faults(data).items():
            if name == "uncommitted-trailer":
                self.assertEqual(changed, data + b"\x01\x02\x03")
            else:
                self.assertEqual(len(changed), len(data))
                self.assertEqual(
                    sum(a != b for a, b in zip(data, changed, strict=True)), 1
                )
                self.assertEqual(
                    (detail["old_byte"] ^ detail["new_byte"]).bit_count(), 1
                )

    def test_full_body_oracle_separates_missing_wrong_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.tsv"
            witness = Path(directory) / "witness.json"
            witness.write_text(
                json.dumps(
                    {
                        "acknowledged_prefix": 3,
                        "bytes_per_payload": 64,
                        "payload_sha256_by_lsn": [
                            hashlib.sha256(payload(i, 64)).hexdigest() for i in range(3)
                        ],
                    }
                )
            )
            path.write_bytes(b"0\t" + payload(0, 64) + b"\n1\t" + b"wrong\n")
            proof = scan_proof(path, 3, 64, witness)
            self.assertEqual(proof["missing_ids"], [2])
            self.assertEqual(proof["wrong_ids"], [1])
            self.assertEqual(proof["bodies_verified"], 1)
            with path.open("ab") as stream:
                stream.write(b"0\t" + payload(0, 64) + b"\n")
            with self.assertRaises(RuntimeError):
                scan_proof(path, 3, 64, witness)

    def test_oracle_uses_saved_witness_and_rejects_mismatched_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path, witness = (
                Path(directory) / "scan.tsv",
                Path(directory) / "witness.json",
            )
            path.write_bytes(b"0\t" + payload(0, 64) + b"\n")
            saved = {
                "acknowledged_prefix": 1,
                "bytes_per_payload": 64,
                "payload_sha256_by_lsn": [hashlib.sha256(payload(99, 64)).hexdigest()],
            }
            witness.write_text(json.dumps(saved))
            self.assertEqual(scan_proof(path, 1, 64, witness)["wrong_ids"], [0])
            saved["acknowledged_prefix"] = 2
            witness.write_text(json.dumps(saved))
            with self.assertRaises(RuntimeError):
                scan_proof(path, 1, 64, witness)


if __name__ == "__main__":
    unittest.main()
