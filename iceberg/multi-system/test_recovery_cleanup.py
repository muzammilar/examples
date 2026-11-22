"""Offline retention gates: never delete a fixture over missing or altered evidence."""

from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import recovery_cleanup as cleanup
import recovery_control as control

RUN = "8be792ce-d8dc-47d8-a65d-3ca3774d7c1e"
OTHER = "e99e83e8-8646-428c-ae53-cfc6f940a333"
THIRD = "79b27e0a-d690-489a-900d-d06917e0f5da"
VERIFY = "af8047d7-861c-43af-9a72-f6ae18a4d228"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_verification(case, identifier, utc):
    workload = case / ("recovery-" + case.name)
    cfg = json.loads((workload / "config.json").read_text())
    checksum = sha(workload / "config.json")
    directory = workload / "verifications" / identifier
    directory.mkdir(parents=True)
    _, names = cleanup.witness_names(cfg)
    body_proof = {
        "rows_verified": 10,
        "acknowledged_rows": 10,
        "canonical_body_sha256": "d" * 64,
    }
    exports = {}
    for engine in ("duckdb", "clickhouse"):
        csv = directory / (engine + "-rows.csv")
        csv.write_text("event_id,body\n1,known\n")
        exported = {
            "run_id": case.name,
            "verification_id": identifier,
            "config_sha256": checksum,
            "csv_file": csv.name,
            "csv_sha256": sha(csv),
            "proof": body_proof,
            "started_ns": utc - 10,
            "ended_ns": utc - 1,
            "duration_ns": 9,
            "native_counters": {},
        }
        write(directory / (engine + "-proof.json"), exported)
        exports[engine] = exported
    write(
        directory / "invocation.json",
        {
            "run_id": case.name,
            "verification_id": identifier,
            "config_sha256": checksum,
            "diagnostic": False,
        },
    )
    value = {
        key: True
        for key in (
            "passed",
            "qualified",
            "integrity_passed",
            "actor_lifecycle_passed",
            "strict_mutation_history_passed",
            "reader_contract_passed",
        )
    }
    value.update(
        run_id=case.name,
        verification_id=identifier,
        config_sha256=checksum,
        diagnostic=False,
        qualification_issues=[],
        utc_ns=utc,
        history_sha256={name: sha(workload / name) for name in names},
        exports=exports,
        snapshot={"table_uuid": "native-" + case.name},
    )
    storage_path = directory / "storage-metrics.json"
    write(
        storage_path,
        {
            "passed": True,
            "run_id": case.name,
            "verification_id": identifier,
            "config_sha256": checksum,
            "snapshot": value["snapshot"],
            "totals": {
                "files": 1,
                "rows": 10,
                "file_bytes": 300,
                "column_chunks": 1,
                "compressed_chunk_bytes": 100,
                "uncompressed_chunk_bytes": 200,
            },
            "files": [{"path": "s3://warehouse/known.parquet"}],
            "encoded_parquet_chunk_compression_ratio": 2.0,
        },
    )
    value["storage_metrics"] = {
        "file": storage_path.name,
        "sha256": sha(storage_path),
        "snapshot": value["snapshot"],
    }
    write(directory / "verified.json", value)
    return {
        "verification_id": identifier,
        "verification_sha256": sha(directory / "verified.json"),
    }


def fixture(root, identifier=RUN, created=10):
    case = root / "results" / "manual" / "production" / identifier
    case.mkdir(parents=True)
    volume = {
        "Name": "owned-storage",
        "CreatedAt": "2026-11-22T00:00:00Z",
        "Mountpoint": "/docker/owned",
    }
    metadata = {
        "kind": "iceberg-storage-crash",
        "project": control.PROJECT,
        "run_id": identifier,
        "created_ns": created,
        "volume": volume,
        "source_sha256": {"app/main.go": "a" * 64},
    }
    write(case / "case.json", metadata)
    cleanup.register_fixture(case)
    write(
        case / "profile.json",
        {
            "images": {
                role: "own-image"
                for role in ("recovery-tools", "recovery-writer", "recovery-reader")
            }
        },
    )
    for name in (
        "resource-summary.json",
        "phase-resources.json",
        "operation-summary.json",
    ):
        write(case / name, {})
    workload = case / ("recovery-" + identifier)
    cfg = {
        "version": 1,
        "run_id": identifier,
        "table": "recovery_" + uuid.UUID(identifier).hex,
        "writers": 2,
        "readers": 2,
        "seed_rows": 10,
        "created_ns": created + 1,
    }
    write(workload / "config.json", cfg)
    checksum = sha(workload / "config.json")
    actors, names = cleanup.witness_names(cfg)
    for name in names:
        if name.endswith(".jsonl"):
            (workload / name).write_text(
                json.dumps({"run_id": identifier, "utc_ns": created + 2}) + "\n"
            )
    write(
        workload / "seed-complete.json",
        {
            "run_id": identifier,
            "config_sha256": checksum,
            "rows": 10,
            "utc_ns": created + 3,
        },
    )
    for actor in actors:
        write(
            workload / (actor + "-ended.json"),
            {
                "run_id": identifier,
                "config_sha256": checksum,
                "clean_shutdown": True,
                "engine": "duckdb" if actor == "reader-000" else "clickhouse",
                "utc_ns": created + 4,
            },
        )
    for number, role in enumerate([*actors, "tool-seed", "tool-verify"]):
        invocation = uuid.uuid4().hex
        directory = case / "processes" / invocation
        service = (
            "recovery-writer"
            if role.startswith("writer")
            else "recovery-reader"
            if role.startswith("reader")
            else "recovery-tools"
        )
        write(
            directory / "launch.json",
            {
                "run_id": identifier,
                "invocation": invocation,
                "service": service,
                "role": role,
                "created_host_ns": created + 5,
                "command": "recovery-seed"
                if role == "tool-seed"
                else "recovery-verify"
                if role == "tool-verify"
                else "recovery-read",
            },
        )
        start = {
            "id": f"client-{number}",
            "image_id": "own-image",
            "restart_count": 0,
            "state": {
                "Running": True,
                "Pid": number + 100,
                "StartedAt": "generation",
                "ExitCode": 0,
                "OOMKilled": False,
            },
            "built_source": {
                "sources": {"main.go": "a" * 64},
                "binary_sha256": "b" * 64,
            },
        }
        write(directory / "started.json", start)
        end = copy.deepcopy(start)
        end.update(clean=True, forced=False, aborted=False)
        end["state"].update(Running=False, Pid=0)
        write(directory / "ended.json", end)
        write(directory / "START", {"released_host_ns": created + 6})
    reference = write_verification(case, VERIFY, created + 100)
    write(
        case / "result.json",
        {"passed": True, **reference, "completed_ns": created + 110},
    )
    return case, metadata


def reverify_receipt(case, stamp, identifier=OTHER):
    reference = write_verification(case, identifier, stamp["latest_operation_ns"] + 100)
    directory = case / "reverifications" / identifier
    invocation = {
        "run_id": case.name,
        "verification_id": identifier,
        "started_ns": stamp["latest_operation_ns"] + 90,
    }
    write(directory / "invocation.json", invocation)
    write(directory / "resources.json", {})
    metadata = json.loads((case / "case.json").read_text())
    manifest = {}
    for name in (
        "case.json",
        "result.json",
        "profile.json",
        "resource-summary.json",
        "phase-resources.json",
        "operation-summary.json",
    ):
        cleanup.add_artifact(manifest, case / name, case)
    cleanup.check_clients(
        case, json.loads((case / "result.json").read_text()), metadata, manifest
    )
    cleanup.check_verification(
        case, reference, metadata, manifest, stamp["latest_operation_ns"]
    )
    cleanup.add_artifact(manifest, directory / "invocation.json", case)
    cleanup.add_artifact(manifest, directory / "resources.json", case)
    write(
        directory / "receipt.json",
        {
            "passed": True,
            **reference,
            "run_id": case.name,
            "invocation": invocation,
            "volume": metadata["volume"],
            "fixture": stamp,
            "case_sha256": sha(case / "case.json"),
            "base_result_sha256": sha(case / "result.json"),
            "artifacts": manifest,
            "completed_ns": stamp["latest_operation_ns"] + 110,
        },
    )
    return directory


class EvidenceGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.patch = patch.object(control, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.case, self.metadata = fixture(self.root)
        self.stamp = cleanup.fixture_stamp([(self.case, self.metadata)])

    def test_complete_case_passes_with_both_csv_and_proof_files(self):
        proof = cleanup.check_case(self.case, self.stamp)
        self.assertEqual(proof["run_id"], RUN)
        self.assertTrue(
            any(name.endswith("duckdb-proof.json") for name in proof["artifacts"])
        )
        self.assertTrue(
            any(name.endswith("clickhouse-rows.csv") for name in proof["artifacts"])
        )

    def test_failed_or_missing_case_cannot_authorize_cleanup(self):
        for name in ("failure.json", "cleanup-errors.json"):
            write(self.case / name, {"error": "preserve"})
            with self.assertRaisesRegex(RuntimeError, "failed recovery"):
                cleanup.check_case(self.case, self.stamp)
            (self.case / name).unlink()
        (self.case / "result.json").unlink()
        with self.assertRaises(FileNotFoundError):
            cleanup.check_case(self.case, self.stamp)

    def test_mutated_or_added_witness_is_not_ignored(self):
        workload = self.case / ("recovery-" + RUN)
        path = workload / "writer-000.jsonl"
        original = path.read_bytes()
        path.write_bytes(original + b"{}\n")
        with self.assertRaisesRegex(RuntimeError, "witness bytes changed"):
            cleanup.check_case(self.case, self.stamp)
        path.write_bytes(original)
        (workload / "unregistered.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(RuntimeError, "witness file set changed"):
            cleanup.check_case(self.case, self.stamp)

    def test_wrong_actor_completion_identity_fails(self):
        path = self.case / ("recovery-" + RUN) / "reader-000-ended.json"
        value = json.loads(path.read_text())
        value["run_id"] = OTHER
        write(path, value)
        with self.assertRaisesRegex(RuntimeError, "actor completion identity"):
            cleanup.check_case(self.case, self.stamp)

    def test_corrupt_csv_or_standalone_engine_proof_fails(self):
        directory = self.case / ("recovery-" + RUN) / "verifications" / VERIFY
        csv = directory / "duckdb-rows.csv"
        body = csv.read_bytes()
        csv.write_bytes(body + b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "CSV/proof changed"):
            cleanup.check_case(self.case, self.stamp)

        csv.write_bytes(body)
        path = directory / "clickhouse-proof.json"
        value = json.loads(path.read_text())
        value["proof"]["rows_verified"] += 1
        write(path, value)
        with self.assertRaisesRegex(RuntimeError, "CSV/proof changed"):
            cleanup.check_case(self.case, self.stamp)

    def test_native_storage_measurement_is_bound_to_verification(self):
        path = (
            self.case
            / ("recovery-" + RUN)
            / "verifications"
            / VERIFY
            / "storage-metrics.json"
        )
        path.write_text("{}\n")
        with self.assertRaisesRegex(
            RuntimeError, "storage measurement evidence changed"
        ):
            cleanup.check_case(self.case, self.stamp)

    def test_stale_original_requires_new_receipt_after_later_fixture(self):
        newer = fixture(self.root, OTHER, created=2000)
        stamp = cleanup.fixture_stamp([(self.case, self.metadata), newer])
        with self.assertRaisesRegex(RuntimeError, "explicit verify-history"):
            cleanup.check_case(self.case, stamp)
        directory = reverify_receipt(self.case, stamp, identifier=THIRD)
        proof = cleanup.check_case(self.case, stamp)
        self.assertEqual(proof["verification_id"], THIRD)
        self.assertTrue((directory / "receipt.json").exists())
        self.assertEqual(
            json.loads((self.case / "result.json").read_text())["verification_id"],
            VERIFY,
        )

    def test_wrong_receipt_identity_cannot_replace_original(self):
        directory = reverify_receipt(self.case, self.stamp)
        path = directory / "receipt.json"
        value = json.loads(path.read_text())
        value["run_id"] = THIRD
        write(path, value)
        with self.assertRaisesRegex(RuntimeError, "receipt identity"):
            cleanup.check_case(self.case, self.stamp)

    def test_stale_receipt_fingerprint_fails_after_another_case(self):
        reverify_receipt(self.case, self.stamp)
        newer = fixture(self.root, THIRD, created=3000)
        stamp = cleanup.fixture_stamp([(self.case, self.metadata), newer])
        with self.assertRaisesRegex(RuntimeError, "stale reverification"):
            cleanup.check_case(self.case, stamp)

    def test_latest_incomplete_reverification_blocks_cleanup(self):
        directory = self.case / "reverifications" / OTHER
        write(
            directory / "invocation.json",
            {"run_id": RUN, "verification_id": OTHER, "started_ns": 10000},
        )
        with self.assertRaises(FileNotFoundError):
            cleanup.check_case(self.case, self.stamp)

    def test_index_refuses_duplicate_torn_and_deleted_registered_case(self):
        with self.assertRaisesRegex(RuntimeError, "already registered"):
            cleanup.register_fixture(self.case)
        index = self.root / "results/fixture-index.jsonl"
        original = index.read_bytes()
        index.write_bytes(original + b"{")
        with self.assertRaisesRegex(RuntimeError, "torn fixture index"):
            cleanup.index_records()
        index.write_bytes(original)
        shutil.rmtree(self.case)
        with self.assertRaises(FileNotFoundError):
            cleanup.cases_by_volume()

    def test_unregistered_directory_blocks_cleanup(self):
        (self.case.parent / OTHER).mkdir()
        with self.assertRaisesRegex(RuntimeError, "missing or unregistered"):
            cleanup.cases_by_volume()

    def test_closed_fixture_checks_are_offline(self):
        checked = cleanup.check_histories()
        directory = self.root / "results/lifecycle" / OTHER
        fixtures = [
            {
                "volume": self.metadata["volume"],
                "cases": checked[cleanup.volume_key(self.metadata["volume"])],
            }
        ]
        write(
            directory / "intent.json", {"action": "docker-down", "fixtures": fixtures}
        )
        write(
            directory / "receipt.json",
            {
                "action": "docker-down",
                "passed": True,
                "project": control.PROJECT,
                "intent_sha256": sha(directory / "intent.json"),
                "fixtures": fixtures,
                "after": {"containers": [], "volumes": [], "networks": []},
                "image_tags_after": {},
            },
        )
        with patch.object(
            control,
            "call",
            side_effect=AssertionError("no Docker/native query allowed"),
        ):
            self.assertEqual(cleanup.check_histories(require_closure=True), checked)

    def test_failed_case_blocks_destructive_commands(self):
        write(self.case / "failure.json", {"error": "native corruption"})
        with (
            patch.object(
                cleanup,
                "project_inventory",
                return_value={"containers": [], "volumes": [], "networks": []},
            ),
            patch.object(control, "call") as mutation,
        ):
            with self.assertRaisesRegex(RuntimeError, "failed recovery"):
                cleanup.teardown("docker-down")
        mutation.assert_not_called()

    def test_unknown_native_uuid_blocks_deletion(self):
        volume = self.metadata["volume"] | {
            "Labels": {"com.docker.compose.volume": "storage"}
        }
        storage = {
            "Id": "owned",
            "Config": {"Labels": {"com.docker.compose.service": "storage"}},
            "State": {"Running": True, "Health": {"Status": "healthy"}},
        }
        with (
            patch.object(
                cleanup,
                "project_inventory",
                return_value={
                    "containers": [storage],
                    "volumes": [volume],
                    "networks": [],
                },
            ),
            patch.object(
                cleanup, "catalog_recoveries", return_value={OTHER: "unregistered"}
            ),
            patch.object(control, "call") as mutation,
        ):
            with self.assertRaisesRegex(RuntimeError, "catalog UUIDs differ"):
                cleanup.teardown("docker-down")
        mutation.assert_not_called()


class LifecycleControls(unittest.TestCase):
    def test_unsupported_python_stops_before_any_native_or_teardown_call(self):
        with (
            patch.object(cleanup.sys, "version_info", (3, 9)),
            patch.object(control, "local_docker") as native,
            patch.object(cleanup, "teardown") as destroy,
        ):
            with self.assertRaisesRegex(RuntimeError, "Python 3.11"):
                cleanup.main()
        native.assert_not_called()
        destroy.assert_not_called()

    def test_catalog_uses_only_static_local_oauth_and_never_follows_redirects(self):
        token = json.dumps({"access_token": "local.token"}) + "\n200"
        namespaces = json.dumps({"namespaces": [["lab"]]}) + "\n200"
        tables = (
            json.dumps(
                {
                    "identifiers": [
                        {"namespace": ["lab"], "name": "recovery_" + uuid.UUID(RUN).hex}
                    ]
                }
            )
            + "\n200"
        )
        metadata = (
            json.dumps(
                {"metadata": {"format-version": 2, "table-uuid": "native-table"}}
            )
            + "\n200"
        )
        with patch.object(
            control, "call", side_effect=[token, namespaces, tables, metadata]
        ) as native:
            self.assertEqual(
                cleanup.catalog_recoveries({"Id": "owned-storage"}),
                {RUN: "native-table"},
            )
        for recorded in native.call_args_list:
            arguments = recorded.args[0]
            self.assertEqual(
                arguments[:5], ["docker", "exec", "owned-storage", "curl", "-q"]
            )
            self.assertNotIn("--location", arguments)
            self.assertIn("--max-filesize", arguments)
            self.assertTrue(arguments[-1].startswith("http://127.0.0.1:8181/v1/"))
        self.assertIn(
            "client_id=demo&client_secret=demo", native.call_args_list[0].args[0][-2]
        )

    def test_catalog_rejects_redirect_and_header_injection_token(self):
        for response in (
            "{}\n302",
            json.dumps({"access_token": "bad\r\nHeader: injected"}) + "\n200",
            json.dumps({"access_token": "x" * 65537}) + "\n200",
        ):
            with (
                self.subTest(response=response),
                patch.object(control, "call", return_value=response) as native,
            ):
                with self.assertRaises(RuntimeError):
                    cleanup.catalog_recoveries({"Id": "owned-storage"})
                native.assert_called_once()

    def test_empty_current_fixture_does_not_launch_a_verifier(self):
        volume = {
            "Name": "owned",
            "CreatedAt": "now",
            "Mountpoint": "/owned",
            "Labels": {"com.docker.compose.volume": "storage"},
        }
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(
                patch.object(control, "ROOT", Path(directory).resolve())
            )
            stack.enter_context(
                patch.object(
                    cleanup,
                    "project_inventory",
                    return_value={
                        "containers": [],
                        "volumes": [volume],
                        "networks": [],
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    control, "service", side_effect=[{"Id": "storage"}, {"Id": "query"}]
                )
            )
            stack.enter_context(
                patch.object(cleanup, "catalog_recoveries", return_value={})
            )
            sampler = stack.enter_context(patch.object(cleanup, "ResourceSampler"))
            self.assertEqual(cleanup.verify_history(), {})
            sampler.assert_not_called()

    def test_interruption_stops_current_verifier_and_never_starts_next_case(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            stack.enter_context(patch.object(control, "ROOT", root))
            case, metadata = fixture(root)
            later, _ = fixture(root, OTHER, created=2000)
            volume = metadata["volume"] | {
                "Labels": {"com.docker.compose.volume": "storage"}
            }
            stack.enter_context(
                patch.object(
                    cleanup,
                    "project_inventory",
                    return_value={
                        "containers": [],
                        "volumes": [volume],
                        "networks": [],
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    control, "service", side_effect=[{"Id": "storage"}, {"Id": "query"}]
                )
            )
            stack.enter_context(
                patch.object(
                    cleanup,
                    "catalog_recoveries",
                    return_value={RUN: "first", OTHER: "second"},
                )
            )
            stopped = stack.enter_context(patch.object(control, "stop_actors"))
            sampler = Mock()
            sampler.close.return_value = ([], {})
            stack.enter_context(
                patch.object(cleanup, "ResourceSampler", return_value=sampler)
            )
            original = KeyboardInterrupt("stop requested")
            stack.enter_context(
                patch.object(cleanup, "check_case", side_effect=original)
            )
            tool = stack.enter_context(patch.object(control, "tool"))
            with self.assertRaises(KeyboardInterrupt) as raised:
                cleanup.verify_history()
            self.assertIs(raised.exception, original)
            sampler.close.assert_called_once()
            stopped.assert_called_once()
            tool.assert_not_called()
            (receipt,) = (case / "reverifications").glob("*/receipt.json")
            self.assertFalse(json.loads(receipt.read_text())["passed"])
            self.assertFalse(list((later / "reverifications").glob("*")))

    def test_history_verification_publishes_receipt_after_final_client_observations(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            stack.enter_context(patch.object(control, "ROOT", root))
            case, metadata = fixture(root)
            original = (case / "result.json").read_bytes()
            volume = metadata["volume"] | {
                "Labels": {"com.docker.compose.volume": "storage"}
            }
            inventory = {"containers": [], "volumes": [volume], "networks": []}
            sampler = Mock()
            sampler.close.return_value = ([], {})
            stack.enter_context(
                patch.object(cleanup, "project_inventory", return_value=inventory)
            )
            stack.enter_context(
                patch.object(
                    cleanup, "catalog_recoveries", return_value={RUN: "native-" + RUN}
                )
            )
            stack.enter_context(
                patch.object(
                    control, "service", side_effect=[{"Id": "storage"}, {"Id": "query"}]
                )
            )
            stack.enter_context(
                patch.object(cleanup, "ResourceSampler", return_value=sampler)
            )
            stack.enter_context(patch.object(control, "profile", return_value={}))

            def tool(case, command, clients, sampler, plan, *, verification):
                reference = write_verification(case, verification, 1000)
                actor = Mock(role="tool-reverify")
                clients["tool-reverify"] = actor
                return reference

            def proof(case, identifier):
                path = (
                    case
                    / ("recovery-" + case.name)
                    / "verifications"
                    / identifier
                    / "verified.json"
                )
                return {"verification_id": identifier, "verification_sha256": sha(path)}

            def stopped(case, clients, sampler):
                # A final observation must precede manifest capture; this mutates an already-fenced process log only.
                path = next((case / "processes").iterdir()) / "last-observation.log"
                path.write_text("positively fenced\n")

            stack.enter_context(patch.object(control, "tool", side_effect=tool))
            stack.enter_context(
                patch.object(control, "verification_proof", side_effect=proof)
            )
            stack.enter_context(
                patch.object(control, "stop_actors", side_effect=stopped)
            )
            checked = cleanup.verify_history()
            self.assertEqual((case / "result.json").read_bytes(), original)
            (receipt,) = (case / "reverifications").glob("*/receipt.json")
            value = json.loads(receipt.read_text())
            self.assertTrue(value["passed"])
            self.assertTrue(
                any(
                    path.endswith("last-observation.log") for path in value["artifacts"]
                )
            )
            self.assertEqual(
                checked[cleanup.volume_key(metadata["volume"])][0]["verification_id"],
                value["verification_id"],
            )
            sampler.close.assert_called_once()

    def test_wrong_image_owner_never_authorizes_removal(self):
        value = {
            "Id": "foreign",
            "Config": {"Labels": {"org.examples.owner": "another-example"}},
        }
        with patch.object(
            control, "call", side_effect=["image-id", json.dumps([value])]
        ) as call:
            with self.assertRaisesRegex(RuntimeError, "ownership label"):
                cleanup.image_inventory()
        self.assertFalse(any("rm" in item.args[0] for item in call.call_args_list))

    def test_remaining_owned_resource_is_not_successful_teardown(self):
        before = {"containers": [], "volumes": [], "networks": []}
        after = before | {"volumes": [{"Name": "still-owned"}]}
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            stack.enter_context(patch.object(control, "ROOT", root))
            stack.enter_context(
                patch.object(cleanup, "cases_by_volume", return_value={})
            )
            stack.enter_context(
                patch.object(cleanup, "project_inventory", side_effect=[before, after])
            )
            stack.enter_context(
                patch.object(cleanup, "image_inventory", return_value={})
            )
            stack.enter_context(
                patch.object(
                    control,
                    "call",
                    return_value=json.dumps(
                        {"services": dict.fromkeys(cleanup.SERVICES)}
                    ),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "remain after teardown"):
                cleanup.teardown("docker-down")
            self.assertFalse(list((root / "results").glob("lifecycle/*/receipt.json")))
            self.assertTrue(list((root / "results").glob("lifecycle/*/intent.json")))

    def test_same_lock_remains_open_during_checks_and_teardown(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            held = []
            stack.enter_context(patch.object(control, "ROOT", root))
            stack.enter_context(
                patch.object(
                    cleanup.fcntl,
                    "flock",
                    side_effect=lambda fd, _: held.append(
                        fd.fileno() if hasattr(fd, "fileno") else fd
                    ),
                )
            )
            stack.enter_context(patch.object(control, "local_docker"))
            stack.enter_context(
                patch.object(
                    cleanup.sys if hasattr(cleanup, "sys") else __import__("sys"),
                    "argv",
                    ["cleanup", "docker-down"],
                )
            )

            def destroy(action):
                self.assertEqual(action, "docker-down")
                self.assertEqual(len(held), 1)
                os.fstat(held[0])

            stack.enter_context(patch.object(cleanup, "teardown", side_effect=destroy))
            cleanup.main()
            with self.assertRaises(OSError):
                os.fstat(held[0])


if __name__ == "__main__":
    unittest.main()
