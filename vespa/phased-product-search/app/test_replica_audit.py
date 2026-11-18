"""Small native-export corruption controls; no database traffic or fixture mutation."""

import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import replica_audit
from version_history import empty


class TinyFixture:
    products = [
        {
            "id": str(i),
            "name": "product",
            "description": "",
            "tenant": 1,
            "stock": 10,
            "price_cents": 100,
            "version": 0,
        }
        for i in range(2)
    ]
    vectors = np.full((2, 384), 0.25, dtype=np.float32)
    manifest = {"sha256": {"tiny-control": "not-a-live-fixture"}}

    def row(self, index, version=0):
        result = self.products[index].copy()
        if version:
            result.update(
                version=version, stock=version % 100, price_cents=100 + version
            )
        return result


class CopyProofTests(unittest.TestCase):
    def test_grouped_audit_rejects_wrong_coverage_with_matching_totals(self):
        class GroupFixture(TinyFixture):
            products = [TinyFixture.products[0] | {"id": str(i)} for i in range(6)]
            vectors = np.full((6, 384), 0.25, dtype=np.float32)

        for duplicate in [False, True]:
            with (
                self.subTest(duplicate=duplicate),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                for name in ["history.json", "floor.json"]:
                    (root / name).write_text(json.dumps(empty()))
                for node in range(9):
                    owned = {2 * (node % 3), 2 * (node % 3) + 1}
                    if duplicate and node == 1:
                        owned = {
                            0,
                            3,
                        }  # Still two rows; group zero duplicates 0 and loses 2.
                    replies = []
                    for index in range(6):
                        if index not in owned:
                            replies.append("Document not found.")
                            continue
                        fields = GroupFixture().row(index)
                        fields.pop("id")
                        fields.pop("description")
                        fields.update(
                            pid=index,
                            embedding={
                                "type": "tensor<float>(x[384])",
                                "values": [0.25] * 384,
                            },
                        )
                        replies.append(
                            json.dumps(
                                {"id": f"id:catalog:product::{index}", "fields": fields}
                            )
                        )
                    with gzip.open(root / f"final-{node}.json.gz", "wt") as stream:
                        stream.write("[\n" + "\n,\n".join(replies) + "\n]")
                with (
                    patch.object(replica_audit, "ROOT", root),
                    patch.object(replica_audit, "COUNT", 9),
                    patch.object(replica_audit, "MODE", "sharded"),
                    patch.object(replica_audit, "Fixture", GroupFixture),
                ):
                    if duplicate:
                        with self.assertRaisesRegex(
                            RuntimeError, "duplicate native copy within group"
                        ):
                            replica_audit.check("final")
                    else:
                        replica_audit.check("final")
                        proof = json.loads((root / "final-proof.json").read_text())
                        self.assertEqual(proof["rows_checked"], 18)
                        self.assertEqual(proof["vector_coordinates_checked"], 6912)
                        self.assertEqual(proof["directed_gets"], 54)

    def audit(self, fault=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["history.json", "floor.json"]:
                (root / name).write_text(json.dumps(empty()))
            docs = []
            for i in range(2):
                fields = TinyFixture.products[i].copy()
                fields.pop("id")
                fields.pop("description")
                fields.update(
                    pid=i,
                    embedding={"type": "tensor<float>(x[384])", "values": [0.25] * 384},
                )
                docs.append({"id": f"id:catalog:product::{i}", "fields": fields})
            if fault == "vector":
                docs[0]["fields"]["embedding"]["values"][383] = 0.5
            if fault == "tensor-type":
                docs[0]["fields"]["embedding"]["type"] = "tensor<double>(x[384])"
            if fault == "metadata":
                docs[0]["fields"]["stock"] = 99
            if fault == "missing-nonempty":
                docs[0]["fields"].pop("name")
            if fault == "missing-copy":
                docs[1] = None
            if fault == "unknown-version":
                docs[0]["fields"].update(version=3, stock=3, price_cents=103)
            with gzip.open(root / "sample-0.json.gz", "wt") as stream:
                json.dump(docs, stream)
            with (
                patch.object(replica_audit, "ROOT", root),
                patch.object(replica_audit, "COUNT", 1),
                patch.object(replica_audit, "MODE", "single"),
                patch.object(replica_audit, "Fixture", TinyFixture),
            ):
                replica_audit.check("sample")
            proof = json.loads((root / "sample-proof.json").read_text())
            self.assertEqual(proof["rows_checked"], 2)
            self.assertEqual(proof["vector_coordinates_checked"], 768)

    def test_ambiguous_versions_must_converge_across_final_copies(self):
        for divergent in [True, False]:
            with (
                self.subTest(divergent=divergent),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                for name in ["history.json", "floor.json"]:
                    (root / name).write_text(json.dumps(empty()))
                intent = {
                    key: value
                    for key, value in TinyFixture().row(0, 1).items()
                    if key in ["id", "tenant", "stock", "price_cents", "version"]
                }
                (root / "writer.jsonl").write_text(
                    "".join(
                        json.dumps({"stage": stage, "rows": [intent], "time": time})
                        + "\n"
                        for stage, time in [("attempt", 1), ("ambiguous", 2)]
                    )
                )
                for node in range(3):
                    docs = []
                    for index in range(2):
                        version = (
                            1 if index == 0 and (not divergent or node == 1) else 0
                        )
                        fields = TinyFixture().row(index, version)
                        fields.pop("id")
                        fields.pop("description")
                        fields.update(
                            pid=index,
                            embedding={
                                "type": "tensor<float>(x[384])",
                                "values": [0.25] * 384,
                            },
                        )
                        docs.append(
                            {"id": f"id:catalog:product::{index}", "fields": fields}
                        )
                    with gzip.open(root / f"final-{node}.json.gz", "wt") as stream:
                        json.dump(docs, stream)
                with (
                    patch.object(replica_audit, "ROOT", root),
                    patch.object(replica_audit, "COUNT", 3),
                    patch.object(replica_audit, "MODE", "cluster"),
                    patch.object(replica_audit, "Fixture", TinyFixture),
                ):
                    if divergent:
                        with self.assertRaisesRegex(RuntimeError, "copies disagree"):
                            replica_audit.check("final")
                    else:
                        replica_audit.check("final")
                        self.assertTrue(
                            json.loads((root / "final-proof.json").read_text())[
                                "all_native_versions_agree"
                            ]
                        )

    def test_complete_metadata_and_every_coordinate(self):
        self.audit()

    def test_wrong_coordinate_metadata_missing_copy_and_unknown_intent_fail(self):
        for fault in [
            "vector",
            "tensor-type",
            "metadata",
            "missing-nonempty",
            "missing-copy",
            "unknown-version",
        ]:
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                self.audit(fault)


if __name__ == "__main__":
    unittest.main()
