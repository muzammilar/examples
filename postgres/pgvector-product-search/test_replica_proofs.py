import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from replica_proofs import audit_copies


class ProofControls(unittest.TestCase):
    def proof(self, root, host, rows):
        path = root / (host + ".jsonl.gz")
        with gzip.open(path, "wt") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        return {
            "rows": 2,
            "vector_coordinates": 768,
            "raw": path.name,
            "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def test_canonical_content_matches_despite_json_key_order(self):
        rows = [dict(id=str(i), version=1, vector_sha256="a" * 64) for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copies = {
                host: self.proof(
                    root,
                    host,
                    [dict(reversed(list(row.items()))) for row in rows]
                    if host == "b"
                    else rows,
                )
                for host in ["a", "b"]
            }
            result = audit_copies(
                {"scope": "all-native-copies", "copies": copies}, root, ["a", "b"], 2
            )
            self.assertTrue(result["all_required_copies_identical"])

    def test_valid_but_different_versions_cannot_pass_as_converged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copies = {}
            for host, version in [("a", 1), ("b", 2)]:
                copies[host] = self.proof(
                    root,
                    host,
                    [
                        dict(id=str(i), version=version, vector_sha256="a" * 64)
                        for i in range(2)
                    ],
                )
            with self.assertRaises(ValueError):
                audit_copies(
                    {"scope": "all-native-copies", "copies": copies},
                    root,
                    ["a", "b"],
                    2,
                )

    def test_missing_duplicate_or_truncated_rows_fail(self):
        for ids in [["0"], ["0", "0"], ["0", "2"]]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                copy = self.proof(
                    root,
                    "a",
                    [dict(id=i, version=1, vector_sha256="a" * 64) for i in ids],
                )
                with self.assertRaises(ValueError):
                    audit_copies(
                        {"scope": "all-native-copies", "copies": {"a": copy}},
                        root,
                        ["a"],
                        2,
                    )

    def test_corrupt_bytes_missing_member_and_unsafe_path_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [dict(id=str(i), vector_sha256="a" * 64) for i in range(2)]
            copy = self.proof(root, "a", rows)
            report = {"scope": "all-native-copies", "copies": {"a": copy}}
            with self.assertRaises(ValueError):
                audit_copies(report, root, ["a", "b"], 2)
            copy["raw"] = "../outside.gz"
            with self.assertRaises(ValueError):
                audit_copies(report, root, ["a"], 2)
            copy["raw"] = "a.jsonl.gz"
            copy["raw_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                audit_copies(report, root, ["a"], 2)


if __name__ == "__main__":
    unittest.main()
