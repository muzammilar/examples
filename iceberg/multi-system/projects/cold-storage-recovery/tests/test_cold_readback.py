"""Reject changed restore lineage, witnesses and partial or relabeled native proofs."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import cold_readback as readback

RUN = "5742e029-c49e-4e57-8832-ddc6495e1e8d"
VERIFICATION = "4d5999d7-6cc2-4d11-82bd-a5d44b733852"
VOLUME = {
    "Name": "source",
    "CreatedAt": "2026-11-22T00:00:00Z",
    "Mountpoint": "/owned/source",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SourceFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.case = self.root / "results/production" / RUN
        self.workload = self.case / ("recovery-" + RUN)
        self.directory = self.workload / "verifications" / VERIFICATION
        self.directory.mkdir(parents=True)
        self.write(self.workload / "config.json", {"run_id": RUN})
        self.write(self.workload / "seed-complete.json", {})
        self.write(self.workload / "writer-000.jsonl", {})
        exports = {}
        for engine in ("duckdb", "clickhouse"):
            path = self.directory / (engine + "-rows.csv")
            path.write_text("complete fixture\n")
            exports[engine] = {
                "csv_file": path.name,
                "csv_sha256": digest(path),
                "proof": {
                    "acknowledged_rows": 2000000,
                    "canonical_body_sha256": "a" * 64,
                },
            }
        self.proof = {
            "run_id": RUN,
            "verification_id": VERIFICATION,
            "config_sha256": digest(self.workload / "config.json"),
            "integrity_passed": True,
            "diagnostic": False,
            "qualified": True,
            "passed": True,
            "exports": exports,
            "snapshot": {"snapshot_id": 123},
            "history_sha256": {
                p.name: digest(p) for p in self.workload.iterdir() if p.is_file()
            },
        }
        self.write(self.directory / "verified.json", self.proof)
        self.patch = patch.object(readback.control, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.registered = patch.object(
            readback.cleanup,
            "cases_by_volume",
            return_value={"volume": [(self.case, {"volume": VOLUME})]},
        )
        self.registered.start()
        self.addCleanup(self.registered.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def load(self):
        return readback.fixture(self.case, VERIFICATION, VOLUME)

    def test_complete_normal_and_diagnostic_baselines_keep_their_verdicts(self):
        self.assertTrue(self.load()[3]["qualified"])
        self.proof.update(diagnostic=True, qualified=False, passed=False)
        self.write(self.directory / "verified.json", self.proof)
        self.assertFalse(self.load()[3]["qualified"])

    def test_changed_witness_and_changed_body_export_are_rejected(self):
        for path in (
            self.workload / "writer-000.jsonl",
            self.directory / "duckdb-rows.csv",
        ):
            with self.subTest(path=path):
                old = path.read_bytes()
                path.write_bytes(old + b"changed\n")
                with self.assertRaises(RuntimeError):
                    self.load()
                path.write_bytes(old)

    def test_foreign_source_volume_and_unregistered_case_are_rejected(self):
        with self.assertRaises(RuntimeError):
            readback.fixture(self.case, VERIFICATION, VOLUME | {"Name": "foreign"})
        with (
            patch.object(readback.cleanup, "cases_by_volume", return_value={}),
            self.assertRaises(RuntimeError),
        ):
            self.load()

    def test_report_presence_or_aggregate_alone_is_not_complete_integrity(self):
        for key, value in (
            ("integrity_passed", False),
            ("config_sha256", "f" * 64),
            ("run_id", str(uuid.uuid4())),
            ("qualified", False),
        ):
            with self.subTest(key=key):
                changed = self.proof | {key: value}
                self.write(self.directory / "verified.json", changed)
                with self.assertRaises(RuntimeError):
                    self.load()
        self.write(self.directory / "verified.json", self.proof)
        self.proof["exports"]["clickhouse"]["proof"] = {"acknowledged_rows": 1999999}
        self.write(self.directory / "verified.json", self.proof)
        with self.assertRaises(RuntimeError):
            self.load()

    def test_persisted_native_body_artifacts_are_rehashed(self):
        readback.verify_exports(self.directory, self.proof, self.case)
        (self.directory / "clickhouse-rows.csv").write_text("damaged")
        with self.assertRaises(RuntimeError):
            readback.verify_exports(self.directory, self.proof, self.case)

    def test_symlinked_journal_is_not_read(self):
        path = self.workload / "writer-000.jsonl"
        path.unlink()
        outside = self.root / "outside"
        outside.write_text("private")
        path.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            self.load()


class CheckpointDecisions(unittest.TestCase):
    def test_current_restore_keeps_pinned_bounded_storage_and_rejects_downgrade(self):
        with patch.object(readback.control.lab, "PROFILE", "current"):
            self.assertEqual(
                readback.restore_files(readback.UPGRADE_454_IMAGE),
                ["-f", readback.TEMPLATE, "-f", "deploy/compose/docker-compose.storage-current.yml"],
            )
            for image in (readback.BASELINE_453_IMAGE, "unverified:latest"):
                with self.subTest(image=image), self.assertRaisesRegex(RuntimeError, "4.54"):
                    readback.restore_files(image)
        with patch.object(readback.control.lab, "PROFILE", "historical"):
            self.assertEqual(
                readback.restore_files(readback.BASELINE_453_IMAGE),
                ["-f", readback.TEMPLATE],
            )

    def test_changed_archive_inventory_and_lineage_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            lab = Path(temporary).resolve()
            identifier = str(uuid.uuid4())
            root = lab / "results/cold-storage" / identifier
            root.mkdir(parents=True)
            name = "iceberg-cold-" + uuid.UUID(identifier).hex
            (root / "storage.tar.gz").write_bytes(b"archive fixture")
            for filename, body in (
                ("files.sha256", "hash ./file\n"),
                ("restored-files.sha256", "hash ./file\n"),
                ("metadata.txt", "f 600 1000 1000 ./file\n"),
                ("restored-metadata.txt", "f 600 1000 1000 ./file\n"),
            ):
                (root / filename).write_text(body)
            values = {
                "result.json": {
                    "passed": True,
                    "run_id": identifier,
                    "restore_volume": name,
                },
                "invocation.json": {
                    "run_id": identifier,
                    "restore_volume": name,
                    "source_volume": {"Name": "source"},
                },
                "restore-volume.json": {
                    "Name": name,
                    "Labels": {readback.cold.LABEL: identifier},
                },
                "archive-proof.json": {
                    "sha256": digest(root / "storage.tar.gz"),
                    "bytes": len(b"archive fixture"),
                    "files_manifest_sha256": digest(root / "files.sha256"),
                    "metadata_manifest_sha256": digest(root / "metadata.txt"),
                },
            }
            for filename, value in values.items():
                (root / filename).write_text(json.dumps(value))
            with patch.object(readback.control, "ROOT", lab):
                self.assertEqual(readback.checkpoint(root)[0], root)
                for filename in (
                    "storage.tar.gz",
                    "files.sha256",
                    "restored-files.sha256",
                    "metadata.txt",
                    "restored-metadata.txt",
                ):
                    with self.subTest(filename=filename):
                        path = root / filename
                        old = path.read_bytes()
                        path.write_bytes(old + b"bad")
                        with self.assertRaises(RuntimeError):
                            readback.checkpoint(root)
                        path.write_bytes(old)
                result = values["result.json"] | {"passed": False}
                (root / "result.json").write_text(json.dumps(result))
                with self.assertRaises(RuntimeError):
                    readback.checkpoint(root)


class RestoreDecisions(unittest.TestCase):
    def test_volume_recreation_foreign_labels_or_driver_change_is_rejected(self):
        expected = VOLUME | {
            "Name": "restored",
            "Labels": {"org.examples.cold-storage": RUN},
            "Driver": "local",
            "Options": None,
        }
        readback.validate_restore_volume(expected, expected)
        for changed in (
            {"CreatedAt": "later"},
            {"Labels": {}},
            {"Options": {"device": "/host"}},
            {"Driver": "nfs"},
        ):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                readback.validate_restore_volume(expected | changed, expected)

    def test_body_snapshot_and_original_failed_verdict_must_match(self):
        proof = {
            "run_id": RUN,
            "verification_id": VERIFICATION,
            "config_sha256": "a" * 64,
            "history_sha256": {"config.json": "a" * 64},
            "integrity_passed": True,
            "qualified": False,
            "diagnostic": True,
            "passed": False,
            "snapshot": {"snapshot_id": 2**60 + 17},
            "exports": {
                e: {
                    "proof": {
                        "canonical_body_sha256": "b" * 64,
                        "acknowledged_rows": 3390000,
                    }
                }
                for e in ("duckdb", "clickhouse")
            },
        }
        baseline = copy.deepcopy(proof)
        readback.validate_proof(proof, baseline, RUN, VERIFICATION, "c" * 64)
        for changed in (
            {"qualified": True},
            {"snapshot": {"snapshot_id": 2**60}},
            {"history_sha256": {}},
            {"integrity_passed": False},
        ):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                readback.validate_proof(
                    proof | changed, baseline, RUN, VERIFICATION, "c" * 64
                )
        proof["exports"]["clickhouse"]["proof"]["canonical_body_sha256"] = "d" * 64
        with self.assertRaises(RuntimeError):
            readback.validate_proof(proof, baseline, RUN, VERIFICATION, "c" * 64)

    def test_failed_inspection_cannot_be_used_as_proof_of_missing_client(self):
        with patch.object(readback.control, "call", return_value=""):
            self.assertIsNone(readback.planned_verifier("exact-name", "project"))
        with (
            patch.object(
                readback.control, "call", side_effect=RuntimeError("runtime unknown")
            ),
            self.assertRaises(RuntimeError),
        ):
            readback.planned_verifier("exact-name", "project")

    def test_network_must_be_internal_and_owned(self):
        value = {"NetworkSettings": {"Networks": {"one": {"NetworkID": "network-id"}}}}
        for internal, project in ((False, "owned"), (True, "foreign")):
            with (
                self.subTest(internal=internal, project=project),
                patch.object(
                    readback.control,
                    "call",
                    return_value=json.dumps(
                        [
                            {
                                "Internal": internal,
                                "Labels": {"com.docker.compose.project": project},
                            }
                        ]
                    ),
                ),
                self.assertRaises(RuntimeError),
            ):
                readback.internal_network(value, "owned")

    def test_foreign_restore_container_is_never_treated_as_owned(self):
        value = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "foreign",
                    "com.docker.compose.service": "verifier",
                }
            }
        }
        with (
            patch.object(readback.cold, "inspect", return_value=value),
            self.assertRaises(RuntimeError),
        ):
            readback.observed("id", "wanted", "verifier")


if __name__ == "__main__":
    unittest.main()
