"""Archive an instrumentation failure without passing the failed experiment."""

from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import recovery_cleanup as cleanup
import recovery_control as control
import recovery_retirement as retirement
from test_recovery_cleanup import (
    fixture,
    sha,
    write,
    write_verification,
    RUN,
    VERIFY,
    OTHER,
    THIRD,
)


def sealed_fixture(root):
    case, metadata = fixture(root)
    (case / "result.json").unlink()
    source = case / "preservation/source"
    source_file = source / "app/main.go"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("original source\n")
    metadata["source_sha256"] = {"app/main.go": sha(source_file)}
    write(case / "case.json", metadata)
    index = root / "results/fixture-index.jsonl"
    records = [json.loads(line) for line in index.read_text().splitlines()]
    records[0]["case_sha256"] = sha(case / "case.json")
    index.write_text("".join(json.dumps(row) + "\n" for row in records))
    write(
        source / "manifest.json",
        {
            "run_id": RUN,
            "original_case_sha256": sha(case / "case.json"),
            "files": {
                "app/main.go": {
                    "sha256": sha(source_file),
                    "bytes": source_file.stat().st_size,
                }
            },
        },
    )
    processes, original_tool = case / "processes", None
    for directory in list(processes.iterdir()):
        for name in ("started.json", "ended.json"):
            value = json.loads((directory / name).read_text())
            value["built_source"]["sources"] = {"main.go": sha(source_file)}
            write(directory / name, value)
        if (
            json.loads((directory / "launch.json").read_text())["command"]
            == "recovery-verify"
        ):
            original_tool = directory
    extra = processes / uuid.uuid4().hex
    extra.mkdir()
    for name in ("launch.json", "started.json", "ended.json"):
        value = json.loads((original_tool / name).read_text())
        if name == "launch.json":
            value["invocation"], value["role"] = extra.name, "tool-diagnostic"
        else:
            value["id"] = "diagnostic-id"
        write(extra / name, value)
    for directory in (original_tool, extra):
        ended = json.loads((directory / "ended.json").read_text())
        ended["clean"], ended["state"]["ExitCode"] = False, 1
        write(directory / "ended.json", ended)
    workload = case / ("recovery-" + RUN)
    old = workload / "verifications" / VERIFY
    (old / "verified.json").unlink()
    inv = json.loads((old / "invocation.json").read_text())
    inv["utc_ns"] = 100
    write(old / "invocation.json", inv)
    raw = {
        "metadata-location": "s3://warehouse/table/metadata/v1.json",
        "metadata": {
            "format-version": 2,
            "table-uuid": "native-" + RUN,
            "current-snapshot-id": retirement.ACTUAL_SNAPSHOT,
        },
    }
    write(case / "preservation/native.json", raw)
    rounded = copy.deepcopy(raw)
    rounded["metadata"]["current-snapshot-id"] = retirement.ROUNDED_SNAPSHOT
    needle = "Could not find snapshot with id " + str(retirement.ROUNDED_SNAPSHOT)
    write(
        case / "failure.json",
        {
            "passed": False,
            "native_storage_preserved": True,
            "error": "recovery-verify: " + needle,
        },
    )
    write(
        case / "cleanup-errors.json", [{"role": "clients", "error": "failed verifier"}]
    )
    for name in retirement.CATALOG_RECORDS:
        write(
            old / name,
            {
                "available": True,
                "body_sha256": sha(case / "preservation/native.json"),
                "load_table": rounded,
            },
        )
    write(old / "failure.json", {"error": needle})
    write(
        old / "storage-metrics.json",
        {
            "passed": False,
            "unavailable_error": needle,
            "snapshot": {
                "table_uuid": "native-" + RUN,
                "snapshot_id": retirement.ROUNDED_SNAPSHOT,
                "metadata_location": raw["metadata-location"],
            },
        },
    )
    seal = case / "preservation/pre-fix/manifest.json"
    files = {
        str(path.relative_to(case)): {"sha256": sha(path), "bytes": path.stat().st_size}
        for path in case.rglob("*")
        if path.is_file()
    }
    write(seal, {"run_id": RUN, "captured_ns": 1000, "files": files})
    return case, metadata, source / "manifest.json", seal


def corrected_proof(case, identifier):
    reference = write_verification(case, identifier, 2000)
    directory = case / ("recovery-" + RUN) / "verifications" / identifier
    proof = json.loads((directory / "verified.json").read_text())
    catalog = json.loads((case / "preservation/native.json").read_text())
    snapshot = {
        "table_uuid": catalog["metadata"]["table-uuid"],
        "snapshot_id": catalog["metadata"]["current-snapshot-id"],
        "metadata_location": catalog["metadata-location"],
    }
    invocation = json.loads((directory / "invocation.json").read_text())
    invocation["utc_ns"] = 1500
    write(directory / "invocation.json", invocation)
    proof["snapshot"] = snapshot
    storage = json.loads((directory / "storage-metrics.json").read_text())
    storage["snapshot"] = snapshot
    write(directory / "storage-metrics.json", storage)
    proof["storage_metrics"].update(
        snapshot=snapshot, sha256=sha(directory / "storage-metrics.json")
    )
    write(directory / "verified.json", proof)
    for name in retirement.CATALOG_RECORDS:
        write(
            directory / name,
            {
                "run_id": RUN,
                "config_sha256": proof["config_sha256"],
                "available": True,
                "body_sha256": sha(case / "preservation/native.json"),
                "load_table": catalog,
            },
        )
    return {**reference, "verification_sha256": sha(directory / "verified.json")}


class RetirementGates(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(control, "ROOT", self.root))
        self.case, self.metadata, self.source, self.original = sealed_fixture(self.root)
        for key, value in {
            "RUN_ID": RUN,
            "ORIGINAL_VERIFY": VERIFY,
            "SOURCE_SHA": sha(self.source),
            "ARTIFACT_SHA": sha(self.original),
            "CATALOG_FILE": "preservation/native.json",
            "CATALOG_SHA": sha(self.case / "preservation/native.json"),
        }.items():
            self.stack.enter_context(patch.object(retirement, key, value))
        self.stamp = cleanup.fixture_stamp([(self.case, self.metadata)])

    def evidence(self):
        return retirement.original_evidence(self.case, self.source, self.original)

    def native(self, identifier, service):
        for directory in (self.case / "processes").iterdir():
            ended = json.loads((directory / "ended.json").read_text())
            if ended["id"] == identifier:
                return {
                    "Id": identifier,
                    "Image": ended["image_id"],
                    "State": ended["state"],
                    "RestartCount": ended["restart_count"],
                    "Config": {
                        "Labels": {
                            "org.examples.recovery.run": RUN,
                            "org.examples.recovery.invocation": directory.name,
                        }
                    },
                }
        raise RuntimeError("inspect failed")

    def receipt(self):
        metadata, _, _, _, _ = self.evidence()
        directory = self.case / "retirements" / OTHER
        invocation = {
            "run_id": RUN,
            "retirement_id": OTHER,
            "verification_id": THIRD,
            "started_ns": 1500,
            "verification_started_ns": 1500,
            "source_manifest": str(self.source.relative_to(self.case)),
            "original_manifest": str(self.original.relative_to(self.case)),
        }
        write(directory / "invocation.json", invocation)
        write(
            directory / "current-profile.json",
            {"images": {"recovery-tools": "own-image"}},
        )
        write(directory / "current-sources.json", metadata["source_sha256"])
        write(directory / "resources.json", {})
        reference = corrected_proof(self.case, THIRD)
        old_tool = next(
            path
            for path in (self.case / "processes").iterdir()
            if json.loads((path / "launch.json").read_text())["command"]
            == "recovery-verify"
        )
        client = self.case / "processes" / uuid.uuid4().hex
        client.mkdir()
        for name in ("launch.json", "started.json", "ended.json"):
            value = json.loads((old_tool / name).read_text())
            if name == "launch.json":
                value.update(
                    invocation=client.name,
                    created_host_ns=1600,
                    extra={"RECOVERY_VERIFICATION_ID": THIRD},
                )
            else:
                value["id"] = "new-id"
                if name == "ended.json":
                    value["clean"], value["state"]["ExitCode"] = True, 0
            write(client / name, value)
        write(
            directory / "current-client.json",
            control.state(self.native("new-id", "recovery-tools")),
        )
        metadata, base, snapshot, bodies, manifest = self.evidence()
        retirement._check_corrected(
            self.case, reference, metadata, snapshot, bodies, manifest, 1500
        )
        for name in (
            "invocation.json",
            "resources.json",
            "current-profile.json",
            "current-sources.json",
            "current-client.json",
        ):
            cleanup.add_artifact(manifest, directory / name, self.case)
        retirement.readonly_artifacts(self.case, manifest, 2500)
        with patch.object(control, "owned", side_effect=self.native):
            fences = retirement.native_fences(self.case, 1000)
            readonly_fences = retirement.readonly_native_fences(self.case, 1000)
        value = {
            "passed": False,
            "data_preservation_passed": True,
            "status": retirement.STATUS,
            **reference,
            "run_id": RUN,
            "retirement_id": OTHER,
            "invocation": invocation,
            "volume": metadata["volume"],
            "case_sha256": sha(self.case / "case.json"),
            "original_process_cutoff_ns": 1000,
            "source_manifest_sha256": sha(self.source),
            "original_manifest_sha256": sha(self.original),
            "native_catalog_body_sha256": sha(self.case / "preservation/native.json"),
            "native_fences": fences,
            "readonly_native_fences": readonly_fences,
            "fixture": self.stamp,
            "artifacts": manifest,
            "completed_ns": 2500,
        }
        write(directory / "receipt.json", value)
        return directory

    def test_valid_archival_keeps_failed_verdict(self):
        directory = self.receipt()
        result = cleanup.check_case(self.case, self.stamp)
        self.assertEqual(result["original_status"], "failed")
        self.assertTrue(result["data_preservation_passed"])
        self.assertFalse(json.loads((directory / "receipt.json").read_text())["passed"])
        self.assertFalse((self.case / "result.json").exists())
        self.assertFalse(json.loads((self.case / "failure.json").read_text())["passed"])

    def test_corrupt_old_csv_source_witness_catalog_rejected(self):
        self.receipt()
        paths = [
            self.source.parent / "app/main.go",
            self.case / "preservation/native.json",
            self.case / ("recovery-" + RUN) / "writer-000.jsonl",
            self.case
            / ("recovery-" + RUN)
            / "verifications"
            / VERIFY
            / "duckdb-rows.csv",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                old = path.read_bytes()
                path.write_bytes(old + b"changed")
                with self.assertRaises(RuntimeError):
                    cleanup.check_case(self.case, self.stamp)
                path.write_bytes(old)

    def test_missing_failed_wrong_identity_receipts_block(self):
        directory = self.receipt()
        path = directory / "receipt.json"
        original = path.read_bytes()
        for change in (
            {"passed": True},
            {"data_preservation_passed": False},
            {"run_id": OTHER},
            {"volume": {"Name": "wrong", "CreatedAt": "x", "Mountpoint": "y"}},
        ):
            with self.subTest(change=change):
                value = json.loads(original)
                value.update(change)
                write(path, value)
                with self.assertRaises(RuntimeError):
                    cleanup.check_case(self.case, self.stamp)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            cleanup.check_case(self.case, self.stamp)

    def test_unfenced_unclean_actor_rejected(self):
        for directory in (self.case / "processes").iterdir():
            if (
                json.loads((directory / "launch.json").read_text())["role"]
                == "writer-000"
            ):
                path = directory / "ended.json"
                break
        value = json.loads(path.read_text())
        value["clean"] = False
        write(path, value)
        with self.assertRaises(RuntimeError):
            self.evidence()
        with self.assertRaisesRegex(
            RuntimeError, "original client did not end cleanly"
        ):
            cleanup.check_clients(
                self.case,
                {"completed_ns": 1000},
                self.metadata,
                {},
                allow_failed_verifiers=True,
            )
        with patch.object(control, "owned", side_effect=RuntimeError("inspect failed")):
            with self.assertRaisesRegex(RuntimeError, "inspect failed"):
                retirement.native_fences(self.case, 1000)

    def test_stale_after_later_case(self):
        self.receipt()
        with self.assertRaisesRegex(RuntimeError, "earlier UUID"):
            cleanup.check_case(
                self.case,
                {**self.stamp, "last_case": OTHER, "latest_operation_ns": 3000},
            )

    def test_incomplete_new_attempt_blocks_old_receipt(self):
        self.receipt()
        write(
            self.case / "retirements" / THIRD / "invocation.json",
            {"run_id": RUN, "retirement_id": THIRD, "started_ns": 3000},
        )
        with self.assertRaises(FileNotFoundError):
            cleanup.check_case(self.case, self.stamp)

    def test_known_failed_identity_cannot_fall_back_to_normal_result(self):
        self.receipt()
        (self.case / "failure.json").unlink()
        (self.case / "cleanup-errors.json").unlink()
        write(self.case / "result.json", {"passed": True})
        with self.assertRaisesRegex(RuntimeError, "identified failed"):
            cleanup.check_case(self.case, self.stamp)

    def test_failed_later_helper_requires_a_current_native_fence(self):
        self.receipt()
        source = next(
            path
            for path in (self.case / "processes").iterdir()
            if json.loads((path / "ended.json").read_text())["id"] == "new-id"
        )
        directory = self.case / "processes" / uuid.uuid4().hex
        directory.mkdir()
        for name in ("launch.json", "started.json", "ended.json"):
            value = json.loads((source / name).read_text())
            if name == "launch.json":
                value.update(
                    invocation=directory.name,
                    role="tool-failed",
                    created_host_ns=1700,
                    extra={"RECOVERY_VERIFICATION_ID": OTHER},
                )
            else:
                value["id"] = "late-failed-id"
                if name == "ended.json":
                    value["clean"], value["state"]["ExitCode"] = False, 1
            write(directory / name, value)
        with patch.object(control, "owned", side_effect=self.native) as inspect:
            fences = retirement.readonly_native_fences(self.case, 1000)
        self.assertEqual(
            {value["id"] for value in fences}, {"new-id", "late-failed-id"}
        )
        self.assertEqual(inspect.call_count, 2)

        def failed_inspect(identifier, service):
            if identifier == "late-failed-id":
                raise RuntimeError("unknown native state")
            return self.native(identifier, service)

        with patch.object(control, "owned", side_effect=failed_inspect):
            with self.assertRaisesRegex(RuntimeError, "unknown native state"):
                retirement.readonly_native_fences(self.case, 1000)
        with self.assertRaisesRegex(
            RuntimeError, "saved later helper native fence set differs"
        ):
            cleanup.check_case(self.case, self.stamp)
        # A genuinely later history verifier belongs to a newer receipt. Its
        # fenced read-only lifecycle must not change the older archived manifest.
        launch = json.loads((directory / "launch.json").read_text())
        launch["created_host_ns"] = 3000
        write(directory / "launch.json", launch)
        cleanup.check_case(self.case, self.stamp)

    def test_corrected_native_body_hash_must_match_original(self):
        self.receipt()
        path = (
            self.case
            / ("recovery-" + RUN)
            / "verifications"
            / THIRD
            / "verify-start-catalog.json"
        )
        value = json.loads(path.read_text())
        value["body_sha256"] = "0" * 64
        write(path, value)
        with self.assertRaisesRegex(RuntimeError, "native catalog changed"):
            cleanup.check_case(self.case, self.stamp)

    def test_unrelated_failed_case_cannot_use_retirement(self):
        with patch.object(retirement, "RUN_ID", OTHER):
            with self.assertRaisesRegex(RuntimeError, "identified failed"):
                self.evidence()

    def test_history_reverification_uses_retirement_as_immutable_base(self):
        retirement_dir = self.receipt()
        stamp = {
            **self.stamp,
            "fingerprint": "new-fixture",
            "last_case": OTHER,
            "latest_operation_ns": 3000,
        }
        sampler = Mock()
        sampler.close.return_value = ([], {})

        def verify(case, command, clients, metrics, plan, *, verification):
            corrected_proof(case, verification)
            directory = case / ("recovery-" + RUN) / "verifications" / verification
            for name, field, value in (
                ("verified.json", "utc_ns", 5000),
                ("invocation.json", "utc_ns", 4000),
            ):
                body = json.loads((directory / name).read_text())
                body[field] = value
                write(directory / name, body)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(cleanup, "project_inventory", return_value={})
            )
            stack.enter_context(
                patch.object(
                    cleanup,
                    "current_volume",
                    return_value=cleanup.volume_key(self.metadata["volume"]),
                )
            )
            stack.enter_context(
                patch.object(cleanup, "fixture_stamp", return_value=stamp)
            )
            stack.enter_context(
                patch.object(control, "service", side_effect=lambda role: {"Id": role})
            )
            stack.enter_context(
                patch.object(
                    cleanup, "catalog_recoveries", return_value={RUN: "native-" + RUN}
                )
            )
            stack.enter_context(patch.object(control, "profile", return_value={}))
            stack.enter_context(patch.object(control, "tool", side_effect=verify))
            stack.enter_context(patch.object(control, "stop_actors"))
            stack.enter_context(
                patch.object(cleanup, "ResourceSampler", return_value=sampler)
            )
            checked = cleanup.verify_history()
        self.assertEqual(next(iter(checked.values()))[0]["original_status"], "failed")
        receipt = next((self.case / "reverifications").glob("*/receipt.json"))
        self.assertEqual(
            json.loads(receipt.read_text())["base_result_sha256"],
            sha(retirement_dir / "receipt.json"),
        )
        self.assertFalse(
            json.loads((retirement_dir / "receipt.json").read_text())["passed"]
        )
        self.assertFalse((self.case / "result.json").exists())

    def test_failed_or_missing_completed_collector_cannot_be_consumed(self):
        directory = self.case / "preservation" / ("corrected-verification-" + THIRD)
        write(
            directory / "invocation.json",
            {"run_id": RUN, "verification_id": THIRD, "started_ns": 10},
        )
        with self.assertRaises(FileNotFoundError):
            retirement.completed_export(self.case, directory, {}, {}, {}, {}, 0)
        write(
            directory / "result.json",
            {"verification_id": THIRD, "data_verification_passed": False},
        )
        with self.assertRaisesRegex(RuntimeError, "did not finalize"):
            retirement.completed_export(self.case, directory, {}, {}, {}, {}, 0)

    def test_fresh_verifier_publishes_only_after_clean_finalization(self):
        sampler = Mock()
        sampler.close.return_value = ([], {})

        def verify(case, command, clients, metrics, plan, *, verification):
            corrected_proof(case, verification)
            old = next(
                path
                for path in (case / "processes").iterdir()
                if json.loads((path / "launch.json").read_text())["command"]
                == "recovery-verify"
            )
            directory = case / "processes" / uuid.uuid4().hex
            directory.mkdir()
            for name in ("launch.json", "started.json", "ended.json"):
                value = json.loads((old / name).read_text())
                if name == "launch.json":
                    value.update(
                        invocation=directory.name,
                        created_host_ns=1600,
                        extra={"RECOVERY_VERIFICATION_ID": verification},
                    )
                else:
                    value["id"] = "new-id"
                    if name == "ended.json":
                        value["clean"], value["state"]["ExitCode"] = True, 0
                write(directory / name, value)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(cleanup, "project_inventory", return_value={})
            )
            stack.enter_context(
                patch.object(
                    cleanup,
                    "current_volume",
                    return_value=cleanup.volume_key(self.metadata["volume"]),
                )
            )
            stack.enter_context(
                patch.object(control, "service", side_effect=lambda role: {"Id": role})
            )
            stack.enter_context(
                patch.object(
                    cleanup, "catalog_recoveries", return_value={RUN: "native-" + RUN}
                )
            )
            stack.enter_context(patch.object(control, "owned", side_effect=self.native))
            stack.enter_context(
                patch.object(
                    control,
                    "profile",
                    return_value={
                        "images": {"recovery-tools": "own-image"},
                        "network_ids": set(),
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    control, "sources", return_value=self.metadata["source_sha256"]
                )
            )
            stack.enter_context(patch.object(control, "wait_coverage"))
            stack.enter_context(
                patch.object(retirement, "ResourceSampler", return_value=sampler)
            )
            stack.enter_context(patch.object(control, "tool", side_effect=verify))
            stack.enter_context(patch.object(control, "stop_actors"))
            stack.enter_context(
                patch.object(retirement.time, "time_ns", side_effect=[1500, 2500])
            )
            receipt = retirement.retire_case(self.case, self.source, self.original)
        self.assertEqual(json.loads(receipt.read_text())["status"], retirement.STATUS)
        self.assertFalse(json.loads(receipt.read_text())["passed"])
        self.assertTrue(json.loads(receipt.read_text())["data_preservation_passed"])
        sampler.close.assert_called_once()
        cleanup.check_case(self.case, self.stamp)

    def test_resource_finalization_failure_cannot_publish_preservation_pass(self):
        sampler = Mock()
        sampler.close.side_effect = RuntimeError("sampler failed")
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(cleanup, "project_inventory", return_value={})
            )
            stack.enter_context(
                patch.object(
                    cleanup,
                    "current_volume",
                    return_value=cleanup.volume_key(self.metadata["volume"]),
                )
            )
            stack.enter_context(
                patch.object(control, "service", side_effect=lambda role: {"Id": role})
            )
            stack.enter_context(
                patch.object(
                    cleanup, "catalog_recoveries", return_value={RUN: "native-" + RUN}
                )
            )
            stack.enter_context(patch.object(control, "owned", side_effect=self.native))
            stack.enter_context(
                patch.object(
                    control,
                    "profile",
                    return_value={"images": {}, "network_ids": set()},
                )
            )
            stack.enter_context(patch.object(control, "sources", return_value={}))
            stack.enter_context(patch.object(control, "wait_coverage"))
            stack.enter_context(
                patch.object(retirement, "ResourceSampler", return_value=sampler)
            )
            stack.enter_context(patch.object(control, "tool"))
            stack.enter_context(
                patch.object(control, "verification_proof", return_value={})
            )
            stack.enter_context(patch.object(control, "stop_actors"))
            with self.assertRaisesRegex(RuntimeError, "sampler failed"):
                retirement.retire_case(self.case, self.source, self.original)
        receipt = next((self.case / "retirements").glob("*/receipt.json"))
        self.assertFalse(json.loads(receipt.read_text())["data_preservation_passed"])

    def test_stop_finalizes_all_and_keeps_primary_failure(self):
        sampler = Mock()
        sampler.close.return_value = ([], {})
        plan = {"images": {"recovery-tools": "new"}, "network_ids": set()}
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(cleanup, "project_inventory", return_value={})
            )
            stack.enter_context(
                patch.object(
                    cleanup,
                    "current_volume",
                    return_value=cleanup.volume_key(self.metadata["volume"]),
                )
            )
            stack.enter_context(
                patch.object(control, "service", side_effect=lambda role: {"Id": role})
            )
            stack.enter_context(
                patch.object(
                    cleanup, "catalog_recoveries", return_value={RUN: "native-" + RUN}
                )
            )
            stack.enter_context(patch.object(control, "owned", side_effect=self.native))
            stack.enter_context(patch.object(control, "profile", return_value=plan))
            stack.enter_context(patch.object(control, "sources", return_value={}))
            stack.enter_context(patch.object(control, "wait_coverage"))
            stack.enter_context(
                patch.object(retirement, "ResourceSampler", return_value=sampler)
            )
            stack.enter_context(
                patch.object(control, "tool", side_effect=KeyboardInterrupt("stop"))
            )
            stop = stack.enter_context(
                patch.object(
                    control,
                    "stop_actors",
                    side_effect=RuntimeError("cleanup also failed"),
                )
            )
            with self.assertRaisesRegex(KeyboardInterrupt, "stop"):
                retirement.retire_case(self.case, self.source, self.original)
        stop.assert_called_once()
        sampler.close.assert_called_once()
        receipts = list((self.case / "retirements").glob("*/receipt.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertFalse(receipt["data_preservation_passed"])
        self.assertIn("KeyboardInterrupt", receipt["error"])
        self.assertIn("cleanup also failed", str(receipt["cleanup_errors"]))


if __name__ == "__main__":
    unittest.main()
