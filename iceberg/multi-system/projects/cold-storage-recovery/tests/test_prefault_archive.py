"""Pin the historical exception, retain its failure, and fail closed on changes."""

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import prefault_archive as archive
import recovery_cleanup as cleanup
import recovery_control as control
from test_recovery_cleanup import fixture, sha, write, RUN, VERIFY, OTHER


def prefault_fixture(root):
    case, metadata = fixture(root)
    (case / "result.json").unlink()
    metadata["kind"] = "iceberg-compactor-crash"
    source = case / "preservation/source/app/main.go"
    source.parent.mkdir(parents=True)
    source.write_text("sealed source")
    metadata["source_sha256"] = {"app/main.go": sha(source)}
    write(case / "case.json", metadata)
    index = root / "results/fixture-index.jsonl"
    entry = json.loads(index.read_text())
    entry["case_sha256"] = sha(case / "case.json")
    index.write_text(json.dumps(entry) + "\n")
    audit_process = next(
        path
        for path in (case / "processes").iterdir()
        if json.loads((path / "launch.json").read_text())["command"]
        == "recovery-verify"
    )
    launch = json.loads((audit_process / "launch.json").read_text())
    launch.update(created_host_ns=350, extra={"RECOVERY_VERIFICATION_ID": VERIFY})
    write(audit_process / "launch.json", launch)
    for role, service in (
        ("baseline-compactor", "recovery-compactor"),
        ("baseline-gate", "compaction-upload-gate"),
    ):
        directory = case / "maintenance" / uuid.uuid4().hex
        for name in ("launch.json", "started.json", "ended.json"):
            value = json.loads((audit_process / name).read_text())
            if name == "launch.json":
                value.update(
                    invocation=directory.name,
                    role=role,
                    service=service,
                    command=role,
                    created_host_ns=150,
                    extra={},
                )
            else:
                value["id"] = role
                if name == "ended.json":
                    value.update(clean=False, aborted=role == "baseline-gate")
                    value["state"]["ExitCode"] = (
                        1 if role == "baseline-compactor" else 0
                    )
            write(directory / name, value)
    write(
        case / "failure.json",
        {
            "passed": False,
            "native_storage_preserved": True,
            "error": "maintenance helper failed: baseline-compactor",
        },
    )
    (case / "phases.jsonl").write_text(
        json.dumps({"phase": "baseline", "host_ns": 100})
        + "\n"
        + json.dumps({"phase": "finished", "host_ns": 200})
        + "\n"
    )
    workload = case / ("recovery-" + RUN)
    exported = workload / "verifications" / VERIFY
    proof_path = exported / "verified.json"
    proof = json.loads(proof_path.read_text())
    proof["utc_ns"] = 800
    write(proof_path, proof)
    for name in (
        "verify-start-catalog.json",
        "verify-duckdb-catalog.json",
        "verify-clickhouse-catalog.json",
    ):
        write(exported / name, {"operation": "append"})
    audit = case / "preservation" / ("data-verification-" + VERIFY)
    write(
        audit / "invocation.json",
        {"run_id": RUN, "verification_id": VERIFY, "started_ns": 300},
    )
    write(
        audit / "result.json",
        {
            "data_verification_passed": True,
            "original_case_passed": False,
            "verification_id": VERIFY,
            "reference": {
                "verification_id": VERIFY,
                "verification_sha256": sha(proof_path),
            },
            "error": None,
        },
    )
    (audit / "resources.jsonl").write_text("native audit counters\n")
    write(
        case / "preservation/source/manifest.json",
        {
            "run_id": RUN,
            "case_sha256": sha(case / "case.json"),
            "files": {
                "app/main.go": {"sha256": sha(source), "bytes": source.stat().st_size}
            },
        },
    )
    files = {
        str(path.relative_to(case)): {"sha256": sha(path), "bytes": path.stat().st_size}
        for path in case.rglob("*")
        if path.is_file()
        and not any(
            path.is_relative_to(directory)
            for directory in (
                case / "preservation",
                audit_process,
                workload / "verifications",
            )
        )
    }
    write(case / "preservation/original.json", {"run_id": RUN, "files": files})
    return case, metadata, audit


class PrefaultArchive(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(control, "ROOT", self.root))
        self.case, self.metadata, self.audit = prefault_fixture(self.root)
        values = {
            "RUN_ID": RUN,
            "AUDIT_ID": VERIFY,
            "SOURCE_FILE": "preservation/source/manifest.json",
            "ORIGINAL_FILE": "preservation/original.json",
            "SOURCE_SHA": sha(self.case / "preservation/source/manifest.json"),
            "ORIGINAL_SHA": sha(self.case / "preservation/original.json"),
        }
        for name, value in values.items():
            self.stack.enter_context(patch.object(archive, name, value))
        self.stack.enter_context(
            patch.object(archive, "AUDIT_SHA", archive.audit_artifacts(self.case)[0])
        )
        self.stack.enter_context(patch.object(cleanup, "PREFAULT_ARCHIVE_ID", RUN))
        self.stamp = {
            "fingerprint": "known",
            "latest_operation_ns": 200,
            "last_case": RUN,
        }

    def native(self, identifier, service):
        for parent in ("processes", "maintenance"):
            for directory in (self.case / parent).iterdir():
                ended = json.loads((directory / "ended.json").read_text())
                if ended["id"] == identifier:
                    return {
                        "Id": identifier,
                        "Image": ended["image_id"],
                        "RestartCount": ended["restart_count"],
                        "State": ended["state"],
                        "Config": {
                            "Labels": {
                                "org.examples.recovery.run": RUN,
                                "org.examples.recovery.invocation": directory.name,
                            }
                        },
                    }
        raise RuntimeError("unknown native state")

    def publish(self):
        for name, value in {
            "project_inventory": {},
            "current_volume": cleanup.volume_key(self.metadata["volume"]),
            "fixture_stamp": self.stamp,
            "catalog_recoveries": {RUN: "native-" + RUN},
        }.items():
            self.stack.enter_context(patch.object(cleanup, name, return_value=value))
        self.stack.enter_context(
            patch.object(control, "service", return_value={"Id": "storage"})
        )
        self.stack.enter_context(
            patch.object(control, "owned", side_effect=self.native)
        )
        return archive.archive_case(self.case, self.audit)

    def test_preserved_data_never_passes_original_unrun_fault(self):
        path = self.publish()
        receipt = json.loads(path.read_text())
        self.assertFalse(receipt["passed"])
        self.assertTrue(receipt["data_preservation_passed"])
        self.assertFalse(receipt["fault_injected"])
        self.assertEqual(len(receipt["native_fences"]), 8)
        self.assertFalse((self.case / "result.json").exists())
        self.assertFalse(json.loads((self.case / "failure.json").read_text())["passed"])
        self.assertEqual(
            cleanup.check_case(self.case, self.stamp)["original_status"], "failed"
        )

    def test_source_witness_csv_and_catalog_tampering_all_block(self):
        self.publish()
        workload = self.case / ("recovery-" + RUN)
        paths = [
            self.case / "preservation/source/app/main.go",
            workload / "writer-000.jsonl",
            workload / "verifications" / VERIFY / "duckdb-rows.csv",
            workload / "verifications" / VERIFY / "verify-start-catalog.json",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"bad")
                with self.assertRaises(RuntimeError):
                    archive.archived_base(self.case)
                path.write_bytes(original)

    def test_unknown_native_state_blocks_and_records_failure(self):
        with patch.object(
            self, "native", side_effect=RuntimeError("unknown native state")
        ):
            with self.assertRaisesRegex(RuntimeError, "unknown native state"):
                self.publish()
        receipt = next((self.case / "retirements").glob("*/receipt.json"))
        self.assertFalse(json.loads(receipt.read_text())["data_preservation_passed"])

    def test_failed_audit_cannot_be_adopted(self):
        path = self.audit / "result.json"
        result = json.loads(path.read_text())
        result["data_verification_passed"] = False
        write(path, result)
        with self.assertRaisesRegex(RuntimeError, "audit artifacts changed"):
            archive.evidence(self.case)

    def test_unexpected_fault_or_new_case_cannot_use_exception(self):
        write(self.case / "compactor-after-kill.json", {})
        with self.assertRaisesRegex(RuntimeError, "only the sealed"):
            archive.evidence(self.case)
        with patch.object(archive, "RUN_ID", OTHER):
            with self.assertRaisesRegex(RuntimeError, "only the sealed"):
                archive.evidence(self.case)

    def test_incomplete_latest_receipt_blocks_old_archive(self):
        self.publish()
        write(
            self.case / "retirements" / OTHER / "invocation.json",
            {"run_id": RUN, "retirement_id": OTHER, "started_ns": 10**30},
        )
        with self.assertRaises(FileNotFoundError):
            archive.archived_base(self.case)

    def test_later_fault_requires_normal_fresh_history(self):
        self.publish()
        with self.assertRaisesRegex(RuntimeError, "earlier UUID"):
            cleanup.check_case(
                self.case,
                {**self.stamp, "last_case": OTHER, "latest_operation_ns": 900},
            )

    def test_wrong_volume_blocks_offline_archive(self):
        path = self.publish()
        result = json.loads(path.read_text())
        result["volume"]["CreatedAt"] = "different"
        write(path, result)
        with self.assertRaisesRegex(RuntimeError, "identity/seal/fence"):
            archive.archived_base(self.case)

    def test_original_actor_state_change_is_not_relaxed(self):
        path = next((self.case / "processes").glob("*/ended.json"))
        original = json.loads(path.read_text())
        original["clean"] = False
        write(path, original)
        with self.assertRaises(RuntimeError):
            archive.evidence(self.case)

    def test_stop_keeps_failed_receipt_and_reraises(self):
        with patch.object(self, "native", side_effect=KeyboardInterrupt("stop")):
            with self.assertRaisesRegex(KeyboardInterrupt, "stop"):
                self.publish()
        path = next((self.case / "retirements").glob("*/receipt.json"))
        self.assertFalse(json.loads(path.read_text())["data_preservation_passed"])

    def test_other_compactor_cases_still_require_fault_evidence(self):
        with patch.object(cleanup, "PREFAULT_ARCHIVE_ID", OTHER):
            with self.assertRaisesRegex(RuntimeError, "missing/non-regular"):
                cleanup.fixture_stamp([(self.case, self.metadata)])


if __name__ == "__main__":
    unittest.main()
