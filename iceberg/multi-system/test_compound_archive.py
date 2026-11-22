"""Negative controls for terminal preservation; no Docker or native workload."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import compound_archive as archive


class CompoundPreservation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        root_patch = patch.object(archive.control, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.case = self.root / "results/production" / archive.RUN_ID
        self.case.mkdir(parents=True)
        self.volume = {
            "Name": "owned",
            "CreatedAt": "2026-11-22T00:00:00Z",
            "Mountpoint": "/owned/storage",
        }
        self.stamp = {
            "fingerprint": "a" * 64,
            "latest_operation_ns": 10,
            "last_case": archive.RUN_ID,
        }
        self.clients = []
        for parent, directories in archive.REGISTRIES.items():
            for name in sorted(directories):
                number = len(self.clients) + 1
                record = {
                    "id": f"{number:064x}",
                    "image_id": "sha256:" + "b" * 64,
                    "restart_count": 0,
                    "state": {
                        "Running": False,
                        "Pid": 0,
                        "ExitCode": 1,
                        "StartedAt": "2026-11-22T00:00:01Z",
                        "FinishedAt": "2026-11-22T00:00:02Z",
                        "OOMKilled": False,
                        "Error": "",
                    },
                }
                launch = {
                    "run_id": archive.RUN_ID,
                    "invocation": name,
                    "name": "owned-" + name,
                    "service": "recovery-tools",
                    "command": "recovery-verify",
                }
                self.clients.append(
                    {
                        "directory": parent + "/" + name,
                        "record": record,
                        "launch": launch,
                    }
                )
        self.evidence = {
            "metadata": {"volume": self.volume},
            "artifacts": {"failure.json": "c" * 64},
            "clients": self.clients,
            "failure_fingerprint": "d" * 64,
        }
        self.preservation = {
            "verification_id": str(uuid.uuid4()),
            "snapshot": {
                "table_uuid": archive.TABLE_UUID,
                "snapshot_id": 123,
                "metadata_location": "owned",
            },
            "external_artifacts": {"results/cold-storage/archive": "e" * 64},
        }

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def raw(self, client):
        record, launch = client["record"], client["launch"]
        return {
            "Id": record["id"],
            "Image": record["image_id"],
            "State": record["state"].copy(),
            "RestartCount": record["restart_count"],
            "Name": "/" + launch["name"],
            "Config": {
                "Labels": {
                    "org.examples.recovery.run": archive.RUN_ID,
                    "org.examples.recovery.invocation": launch["invocation"],
                }
            },
        }

    def fence(self):
        return {
            "all_container_ids": [],
            "running_run_ids": [],
            "observed_ns": 20,
            "fences": [
                {"directory": c["directory"], "record": c["record"], "native": None}
                for c in self.clients
            ],
        }

    def receipt(self, started=15):
        directory = self.case / archive.DIRECTORY / str(uuid.uuid4())
        invocation = {
            "run_id": archive.RUN_ID,
            "archive_id": directory.name,
            "started_ns": started,
        }
        receipt = {
            "passed": False,
            "status": archive.STATUS,
            "original_status": "failed",
            "data_preservation_passed": True,
            "run_id": archive.RUN_ID,
            "archive_id": directory.name,
            "case_sha256": archive.CASE_SHA,
            "source_seal_sha256": archive.SOURCE_SHA,
            "original_seal_sha256": archive.ORIGINAL_SHA,
            "invocation": invocation,
            "artifacts": self.evidence["artifacts"],
            "failure_fingerprint": self.evidence["failure_fingerprint"],
            "artifacts_fingerprint": archive._hash(self.evidence["artifacts"]),
            "preservation_fingerprint": archive._hash(self.preservation),
            "preservation": self.preservation,
            "volume": self.volume,
            "fixture": self.stamp,
            "native_fences": self.fence(),
            "native_tables": archive.EXPECTED_TABLES,
            "native_heads": self.heads(),
            "completed_ns": 30,
        }
        self.write(directory / "invocation.json", invocation)
        self.write(directory / "receipt.json", receipt)
        return directory, receipt

    def heads(self):
        return {
            run: self.preservation["snapshot"]
            if run == archive.RUN_ID
            else {
                "table_uuid": table,
                "snapshot_id": 456,
                "metadata_location": "other-owned",
            }
            for run, table in archive.EXPECTED_TABLES.items()
        }

    def replay(self, stamp=None):
        with (
            patch.object(archive, "original_evidence", return_value=self.evidence),
            patch.object(archive, "_preservation", return_value=self.preservation),
        ):
            return archive.preserved_case(self.case, stamp or self.stamp)

    def test_preservation_keeps_operational_failure_and_summary_shape(self):
        self.receipt()
        summary = self.replay()
        self.assertEqual(summary["original_status"], "failed")
        self.assertTrue(summary["data_preservation_passed"])
        self.assertNotIn("qualified", summary)
        self.assertEqual(summary["table_uuid"], archive.TABLE_UUID)
        self.assertIn("@external:results/cold-storage/archive", summary["artifacts"])

    def test_missing_latest_failed_or_partial_attempt_blocks_older_success(self):
        with self.assertRaisesRegex(RuntimeError, "completed preservation"):
            self.replay()
        self.receipt()
        newer, receipt = self.receipt(started=19)
        receipt.update(status="failed-preservation", data_preservation_passed=False)
        self.write(newer / "receipt.json", receipt)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.replay()
        (newer / "receipt.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.replay()

    def test_verdict_evidence_volume_and_global_fixture_changes_reject(self):
        directory, receipt = self.receipt()
        changes = [
            {"passed": True},
            {"status": "passed"},
            {"data_preservation_passed": False},
            {"original_status": "passed"},
            {"artifacts": {}},
            {"failure_fingerprint": "changed"},
            {"preservation": {}},
            {"native_tables": {}},
            {"volume": self.volume | {"CreatedAt": "new"}},
            {"fixture": self.stamp | {"fingerprint": "new"}},
        ]
        for change in changes:
            self.write(directory / "receipt.json", receipt | change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.replay()

    def test_source_case_path_cannot_escape_or_alias_results(self):
        alias = self.case.parent / "alias"
        alias.symlink_to(self.case, target_is_directory=True)
        for path in (
            alias,
            self.root / archive.RUN_ID,
            self.case.parent / str(uuid.uuid4()),
        ):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                archive._case(path)

    def test_fingerprint_excludes_only_its_own_archive_attempts(self):
        artifacts = {
            "failure.json": "a",
            "preservation/storage-oom-before-restart.json": "b",
            "maintenance/job/ended.json": "c",
        }
        with patch.object(
            archive,
            "original_evidence",
            return_value={"artifacts": artifacts, "latest_operation_ns": 23},
        ):
            fingerprint = archive.fingerprint(self.case)
        self.assertEqual(fingerprint["latest_operation_ns"], 23)
        self.assertTrue(
            all(
                n.startswith("results/production/" + archive.RUN_ID + "/")
                for n in fingerprint["files"]
            )
        )
        (self.case / "failure.json").write_text("failed")
        self.write(
            self.case / archive.DIRECTORY / str(uuid.uuid4()) / "invocation.json", {}
        )
        self.assertEqual(set(archive._files(self.case)), {"failure.json"})

    def test_pinned_seal_body_count_and_paths_are_enforced(self):
        file = self.case / "body.txt"
        file.write_text("known failed state")
        checksum = hashlib.sha256(file.read_bytes()).hexdigest()
        seal = {
            "run_id": archive.RUN_ID,
            "files": {"body.txt": {"sha256": checksum, "bytes": file.stat().st_size}},
        }
        path = self.case / "seal.json"
        self.write(path, seal)
        files = archive._files(self.case)
        archive._seal(self.case, "seal.json", files["seal.json"], 1, files)
        for sha, count, changed in (
            ("wrong", 1, files),
            (files["seal.json"], 2, files),
            (files["seal.json"], 1, files | {"body.txt": "wrong"}),
        ):
            with self.subTest(count=count, sha=sha), self.assertRaises(RuntimeError):
                archive._seal(self.case, "seal.json", sha, count, changed)
        seal["files"] = {"../outside": {"sha256": checksum, "bytes": 1}}
        self.write(path, seal)
        files = archive._files(self.case)
        with self.assertRaisesRegex(RuntimeError, "escaped"):
            archive._seal(self.case, "seal.json", files["seal.json"], 1, files)

    def test_wrong_checkpoint_volume_archive_or_source_audit_is_rejected(self):
        parent = self.root / "results/cold-storage" / archive.CHECKPOINT_ID
        checkpoint = {"source_volume": self.volume}
        with (
            patch.object(
                archive.readback,
                "checkpoint",
                return_value=(
                    parent,
                    checkpoint
                    | {"source_volume": self.volume | {"CreatedAt": "different"}},
                    {},
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "original native fixture"),
        ):
            archive._preservation(self.case, self.evidence["metadata"])
        with (
            patch.object(
                archive.readback, "checkpoint", return_value=(parent, checkpoint, {})
            ),
            patch.object(archive.cleanup, "digest", return_value="wrong"),
            self.assertRaisesRegex(RuntimeError, "original native fixture"),
        ):
            archive._preservation(self.case, self.evidence["metadata"])
        with (
            patch.object(
                archive.readback, "checkpoint", return_value=(parent, checkpoint, {})
            ),
            patch.object(
                archive.cleanup, "digest", side_effect=[archive.ARCHIVE_SHA, "wrong"]
            ),
            patch.object(
                archive.readback,
                "fixture",
                return_value=(None, None, self.case / "wrong.json", None, None),
            ),
            self.assertRaisesRegex(RuntimeError, "source diagnostic"),
        ):
            archive._preservation(self.case, self.evidence["metadata"])

    def test_exact_registry_and_positive_saved_fences_are_required(self):
        originals = {c["directory"] for c in self.clients}
        for client in self.clients:
            directory = self.case / client["directory"]
            self.write(directory / "launch.json", client["launch"])
            self.write(directory / "started.json", client["record"])
            self.write(directory / "ended.json", client["record"])
        self.assertEqual(len(archive._clients(self.case, originals)), 17)
        first = self.case / self.clients[0]["directory"]
        ended = copy.deepcopy(self.clients[0]["record"])
        ended["state"]["Pid"] = 1
        self.write(first / "ended.json", ended)
        with self.assertRaises(RuntimeError):
            archive._clients(self.case, originals)
        self.write(first / "ended.json", self.clients[0]["record"])
        (self.case / "processes/unregistered").mkdir()
        with self.assertRaisesRegex(RuntimeError, "13\\+4"):
            archive._clients(self.case, originals)

    def test_fresh_native_fences_match_ids_images_start_and_run_labels(self):
        raws = {c["record"]["id"]: self.raw(c) for c in self.clients}
        with (
            patch.object(archive.control, "call", side_effect=["\n".join(raws), ""]),
            patch.object(
                archive.control,
                "owned",
                side_effect=lambda identifier, _: raws[identifier],
            ),
        ):
            saved = archive._native_fences(self.clients)
        archive._check_fences(self.clients, saved)
        saved["fences"][0]["native"]["image_id"] = "wrong"
        with self.assertRaises(RuntimeError):
            archive._check_fences(self.clients, saved)

    def test_native_inspection_failure_never_proves_absence(self):
        identifier = self.clients[0]["record"]["id"]
        with (
            patch.object(archive.control, "call", side_effect=[identifier, ""]),
            patch.object(
                archive.control, "owned", side_effect=RuntimeError("inspection failed")
            ),
            self.assertRaisesRegex(RuntimeError, "inspection failed"),
        ):
            archive._native_fences(self.clients)
        with (
            patch.object(
                archive.control, "call", side_effect=RuntimeError("listing failed")
            ),
            self.assertRaisesRegex(RuntimeError, "listing failed"),
        ):
            archive._native_fences(self.clients)

    def test_healthy_absence_needs_no_inspect_and_unknown_live_actor_blocks(self):
        with (
            patch.object(archive.control, "call", side_effect=["", ""]),
            patch.object(archive.control, "owned") as inspect,
        ):
            saved = archive._native_fences(self.clients)
        inspect.assert_not_called()
        archive._check_fences(self.clients, saved)
        with (
            patch.object(archive.control, "call", side_effect=["", "foreign-live-id"]),
            self.assertRaisesRegex(RuntimeError, "live actor"),
        ):
            archive._native_fences(self.clients)
        saved["all_container_ids"] = [self.clients[0]["record"]["id"]]
        with self.assertRaises(RuntimeError):
            archive._check_fences(self.clients, saved)

    def test_native_name_or_state_drift_is_not_a_fence(self):
        client = self.clients[0]
        for field in ("name", "state", "label"):
            raw = self.raw(client)
            if field == "name":
                raw["Name"] = "/foreign"
            if field == "state":
                raw["State"]["StartedAt"] = "2026-11-22T00:00:03Z"
            if field == "label":
                raw["Config"]["Labels"]["org.examples.recovery.run"] = "foreign"
            with (
                patch.object(
                    archive.control, "call", side_effect=[client["record"]["id"], ""]
                ),
                patch.object(archive.control, "owned", return_value=raw),
                self.subTest(field=field),
                self.assertRaises(RuntimeError),
            ):
                archive._native_fences(self.clients)

    def test_publication_uses_global_fixture_and_keeps_passed_false(self):
        groups = {
            archive.cleanup.volume_key(self.volume): [
                (self.case.parent / name, {}) for name in archive.EXPECTED_TABLES
            ]
        }
        with (
            patch.object(archive, "original_evidence", return_value=self.evidence),
            patch.object(archive, "_preservation", return_value=self.preservation),
            patch.object(archive.cleanup, "project_inventory", return_value={}),
            patch.object(
                archive.cleanup,
                "current_volume",
                return_value=archive.cleanup.volume_key(self.volume),
            ),
            patch.object(archive.cleanup, "cases_by_volume", return_value=groups),
            patch.object(
                archive.cleanup,
                "catalog_recoveries",
                return_value=self.heads(),
            ),
            patch.object(archive.control, "service", return_value={}),
            patch.object(
                archive.cleanup, "fixture_stamp", return_value=self.stamp
            ) as stamp,
            patch.object(
                archive,
                "_native_fences",
                side_effect=lambda _: self.fence()
                | {"observed_ns": archive.time.time_ns()},
            ),
        ):
            receipt_path = archive.archive_case(self.case)
        stamp.assert_called_once_with(next(iter(groups.values())))
        receipt = json.loads(receipt_path.read_text())
        self.assertFalse(receipt["passed"])
        self.assertTrue(receipt["data_preservation_passed"])
        self.assertEqual(receipt["status"], "failed-but-preserved")

    def test_failed_publication_leaves_a_blocking_failed_receipt(self):
        with (
            patch.object(archive, "original_evidence", return_value=self.evidence),
            patch.object(archive, "_preservation", return_value=self.preservation),
            patch.object(
                archive.cleanup,
                "project_inventory",
                side_effect=RuntimeError("native observation lost"),
            ),
            self.assertRaisesRegex(RuntimeError, "native observation lost"),
        ):
            archive.archive_case(self.case)
        receipt = next((self.case / archive.DIRECTORY).glob("*/receipt.json"))
        self.assertFalse(json.loads(receipt.read_text())["data_preservation_passed"])
        with self.assertRaises(RuntimeError):
            self.replay()

    def test_native_head_change_after_fencing_blocks_publication(self):
        groups = {
            archive.cleanup.volume_key(self.volume): [
                (self.case.parent / name, {}) for name in archive.EXPECTED_TABLES
            ]
        }
        changed = copy.deepcopy(self.heads())
        changed[archive.RUN_ID]["snapshot_id"] += 1
        with (
            patch.object(archive, "original_evidence", return_value=self.evidence),
            patch.object(archive, "_preservation", return_value=self.preservation),
            patch.object(archive.cleanup, "project_inventory", return_value={}),
            patch.object(
                archive.cleanup,
                "current_volume",
                return_value=archive.cleanup.volume_key(self.volume),
            ),
            patch.object(archive.cleanup, "cases_by_volume", return_value=groups),
            patch.object(
                archive.cleanup,
                "catalog_recoveries",
                side_effect=[self.heads(), changed],
            ),
            patch.object(archive.control, "service", return_value={}),
            patch.object(archive.cleanup, "fixture_stamp", return_value=self.stamp),
            patch.object(archive, "_native_fences", return_value=self.fence()),
            self.assertRaisesRegex(RuntimeError, "head changed"),
        ):
            archive.archive_case(self.case)
        receipt = next((self.case / archive.DIRECTORY).glob("*/receipt.json"))
        self.assertFalse(json.loads(receipt.read_text())["data_preservation_passed"])


if __name__ == "__main__":
    unittest.main()
