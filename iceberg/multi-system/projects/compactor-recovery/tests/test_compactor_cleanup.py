"""Offline compactor faults and the bounded shared profile integration."""

from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import compactor_evidence as evidence
import recovery_cleanup as cleanup
import recovery_control as control
from test_recovery_cleanup import fixture, sha, write, RUN, OTHER
from test_recovery_control import container


def ref(case, path):
    return {"file": str(path.relative_to(case)), "sha256": sha(path)}


def native(identifier, image="image", running=True, code=0):
    return {
        "id": identifier,
        "image_id": image,
        "restart_count": 0,
        "state": {
            "Running": running,
            "Pid": 123 if running else 0,
            "ExitCode": code,
            "StartedAt": identifier,
            "OOMKilled": False,
        },
    }


def complete_fixture(root):
    case, metadata = fixture(root)
    metadata.update(
        kind="iceberg-compactor-crash", storage=native("storage"), query=native("query")
    )
    metadata["source_sha256"].update(
        {
            "iceberg-compaction/compact.py": "b" * 64,
            "compaction-upload-gate/main.go": "c" * 64,
        }
    )
    write(case / "case.json", metadata)
    index = root / "results/fixture-index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    rows[0]["case_sha256"] = sha(case / "case.json")
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    profile = json.loads((case / "profile.json").read_text())
    profile["images"].update(
        {"recovery-compactor": "spark", "compaction-upload-gate": "gate"}
    )
    write(case / "profile.json", profile)
    workload = case / ("recovery-" + RUN)
    checksum = sha(workload / "config.json")
    result = json.loads((case / "result.json").read_text())
    result["completed_ns"] = 2000
    prefix = "s3://warehouse/lab/recovery_" + uuid.UUID(RUN).hex + "-" + OTHER + "/"
    final = workload / "verifications" / result["verification_id"]
    proof = json.loads((final / "verified.json").read_text())
    proof["snapshot"] = {
        "table_uuid": OTHER,
        "snapshot_id": 20,
        "metadata_location": prefix + "metadata/v20.metadata.json",
    }
    storage = json.loads((final / "storage-metrics.json").read_text())
    storage["snapshot"] = proof["snapshot"]
    write(final / "storage-metrics.json", storage)
    proof["storage_metrics"].update(
        snapshot=proof["snapshot"], sha256=sha(final / "storage-metrics.json")
    )
    proof["utc_ns"] = 1800
    write(final / "verified.json", proof)
    result["verification_sha256"] = sha(final / "verified.json")
    envelope = {
        "version": 1,
        "run_id": RUN,
        "config_sha256": checksum,
        "fault_started_ns": 1300,
        "fault_completed_ns": 1310,
        "inspections": {},
    }
    baseline_id = str(uuid.uuid4())
    body = {"rows": 10, "seed_rows": 10, "batches": 1, "body_sha256": "1" * 64}
    pin = {
        "observation_id": baseline_id,
        "snapshot": {
            "table_uuid": OTHER,
            "snapshot_id": 10,
            "metadata_location": prefix + "metadata/v10.metadata.json",
        },
        "proof": body,
    }
    uri = prefix + "data/candidate.parquet"
    operations = [
        {"snapshot_id": 9, "operation": "replace"},
        {"snapshot_id": 10, "operation": "append"},
    ]
    template = next(
        path
        for path in (case / "processes").iterdir()
        if json.loads((path / "launch.json").read_text())["command"]
        == "recovery-verify"
    )
    for number, phase in enumerate(evidence.PHASES):
        identifier = baseline_id if not number else str(uuid.uuid4())
        if number:
            operations.append(
                {
                    "snapshot_id": 10 + number,
                    "operation": "replace" if phase == "recovered" else "append",
                }
            )
        started = (1000, 1200, 1400, 1600)[number]
        catalog = {
            "table_uuid": OTHER,
            "snapshot_id": 10 + number,
            "metadata_location": prefix + f"metadata/v{10 + number}.metadata.json",
        }
        observation = workload / "observations" / identifier
        report = {
            "passed": True,
            "run_id": RUN,
            "config_sha256": checksum,
            "observation_id": identifier,
            "phase": phase,
            "started_ns": started,
            "ended_ns": started + 10,
            "catalog": catalog,
            "operations": copy.deepcopy(operations),
            "pin": pin,
        }
        if phase == "candidate":
            report["candidate"] = {
                "uri": uri,
                "proof": body,
                "parquet": {
                    "path": uri,
                    "rows": 10,
                    "manifest_record_count": 10,
                    "file_bytes": 1000,
                    "footer_bytes": 100,
                    "groups": [
                        {
                            "compression": "ZSTD",
                            "encodings": "PLAIN",
                            "column_chunks": 7,
                            "compressed_bytes": 800,
                            "uncompressed_bytes": 2000,
                        }
                    ],
                },
            }
        write(observation / "inspection.json", report)
        write(
            observation / "catalog.json",
            {
                "available": True,
                "run_id": RUN,
                "config_sha256": checksum,
                "body_sha256": "f" * 64,
                "load_table": {
                    "metadata-location": catalog["metadata_location"],
                    "metadata": {
                        "format-version": 2,
                        "table-uuid": OTHER,
                        "current-snapshot-id": catalog["snapshot_id"],
                        "snapshots": [
                            {
                                "snapshot-id": row["snapshot_id"],
                                "summary": {"operation": row["operation"]},
                            }
                            for row in operations
                        ],
                    },
                },
            },
        )
        envelope["inspections"][phase] = ref(case, observation / "inspection.json")
        process = case / "processes" / uuid.uuid4().hex
        for name in ("launch.json", "started.json", "ended.json"):
            value = json.loads((template / name).read_text())
            if name == "launch.json":
                value.update(
                    invocation=process.name,
                    role="tool-" + phase,
                    command="recovery-inspect",
                    extra={
                        "RECOVERY_OBSERVATION_ID": identifier,
                        "RECOVERY_INSPECTION_PHASE": phase,
                    },
                )
                if number:
                    value["extra"]["RECOVERY_PIN_OBSERVATION"] = baseline_id
                if phase == "candidate":
                    value["extra"]["RECOVERY_CANDIDATE_URI"] = uri
            else:
                value["id"] = identifier
            write(process / name, value)
    for key in (
        "fault_invocation",
        "recovery_invocation",
        "gate_invocation",
        "baseline_compactor_invocation",
        "baseline_gate_invocation",
    ):
        service = "compaction-upload-gate" if "gate" in key else "recovery-compactor"
        identifier = uuid.uuid4().hex
        envelope[key] = identifier
        directory = case / "maintenance" / identifier
        started = native(key, profile["images"][service])
        ended = native(
            key,
            profile["images"][service],
            False,
            137 if key == "fault_invocation" else 0,
        )
        ended.update(
            clean=key != "fault_invocation",
            forced=key == "fault_invocation",
            aborted=False,
        )
        prefix_source = (
            "compaction-upload-gate/" if "gate" in key else "iceberg-compaction/"
        )
        sources = {
            name: value
            for name, value in metadata["source_sha256"].items()
            if name.startswith(prefix_source)
        }
        write(
            directory / "launch.json",
            {"run_id": RUN, "invocation": identifier, "service": service},
        )
        write(
            directory / "started.json",
            {**started, "built_source": {"sources": sources}},
        )
        write(directory / "ended.json", ended)
        if key == "fault_invocation":
            for field, value in (
                ("fault_before", started),
                (
                    "fault_after",
                    {
                        key: ended[key]
                        for key in ("id", "image_id", "state", "restart_count")
                    },
                ),
            ):
                write(case / (field + ".json"), value)
                envelope[field] = ref(case, case / (field + ".json"))
    survivors = {"storage": metadata["storage"], "query": metadata["query"]}
    for directory in (case / "processes").iterdir():
        launch = json.loads((directory / "launch.json").read_text())
        if launch["role"] in evidence.SURVIVORS:
            survivors[launch["role"]] = json.loads(
                (directory / "started.json").read_text()
            )
    for name in ("survivor_before", "survivor_after", "survivor_final"):
        write(case / (name + ".json"), survivors)
        envelope[name] = ref(case, case / (name + ".json"))
    write(
        case / "service_survivor_final.json",
        {key: survivors[key] for key in ("storage", "query")},
    )
    envelope["service_survivor_final"] = ref(case, case / "service_survivor_final.json")
    for key, hold in (("gate_config", True), ("baseline_gate_config", False)):
        directory = (
            case
            / "maintenance"
            / envelope["gate_invocation" if hold else "baseline_gate_invocation"]
            / "gate"
            / ("upload-gate-" + RUN)
        )
        write(
            directory / "config.json",
            {"run_id": RUN, "object_prefix": prefix + "data/", "hold_final_acks": hold},
        )
        envelope[key] = ref(case, directory / "config.json")
    gate_path = (
        case / envelope["gate_config"]["file"]
    ).parent / "requests/request-test/backend-complete.json"
    gate = {
        "version": 1,
        "run_id": RUN,
        "config_sha256": envelope["gate_config"]["sha256"],
        "object_prefix": prefix + "data/",
        "record_id": "request-test",
        "event": "backend-complete-ack-held",
        "ack_held": True,
        "object_uri": uri,
        "method": "POST",
        "operation": "complete-multipart-upload",
        "backend_status": 200,
        "backend_completed_ns": 1100,
        "journal_file": str(gate_path.relative_to(gate_path.parents[3])),
        "request_content_length": 40,
        "streamed_request_bytes": 40,
    }
    write(gate_path, gate)
    write(
        case / "gate-status.json",
        {
            "version": 1,
            "run_id": RUN,
            "config_sha256": gate["config_sha256"],
            "object_prefix": prefix + "data/",
            "active_holds": [gate],
            "hold_final_acks": True,
            "fatal_error": "",
            "stopping": False,
        },
    )
    envelope["gate_record"], envelope["gate_status"] = (
        ref(case, gate_path),
        ref(case, case / "gate-status.json"),
    )
    baseline_path = (
        case / envelope["baseline_gate_config"]["file"]
    ).parent / "requests/request-baseline/backend-complete.json"
    forwarded = {
        **gate,
        "event": "backend-complete-ack-forwarded",
        "ack_held": False,
        "config_sha256": envelope["baseline_gate_config"]["sha256"],
        "backend_completed_ns": 900,
        "journal_file": str(baseline_path.relative_to(baseline_path.parents[3])),
    }
    write(baseline_path, forwarded)
    envelope["baseline_gate_record"] = ref(case, baseline_path)
    for key in ("baseline_report", "recovered_report"):
        report_id = str(uuid.uuid4())
        invocation = envelope[
            "baseline_compactor_invocation"
            if key == "baseline_report"
            else "recovery_invocation"
        ]
        report_path = (
            case
            / "maintenance"
            / invocation
            / "spark"
            / ("compaction-" + report_id + "-resources.json")
        )
        effective = {
            "live_writers": True,
            "rewrite_manifests": False,
            "s3_endpoint": "http://compaction-upload-gate:8333"
            if key == "baseline_report"
            else "http://storage:8333",
            "catalog_url": "http://storage:8181",
            "rewrite_options": {
                "partial-progress.enabled": "false",
                "max-concurrent-file-group-rewrites": "1",
            },
        }
        write(
            report_path,
            {
                "run_id": report_id,
                "passed": True,
                "errors": [],
                "configuration": effective,
                "stop": {"attempted": True, "passed": True},
                "compaction_result": {
                    "spark_version": "4.0.1",
                    "iceberg_version": "1.10.1",
                    "configuration": effective,
                    "table": "lab.recovery_" + uuid.UUID(RUN).hex,
                    "before": {
                        "snapshot": 9 if key == "baseline_report" else 12,
                        "rows": 10,
                    },
                    "after": {
                        "snapshot": 10 if key == "baseline_report" else 13,
                        "rows": 20,
                    },
                    "retained_snapshot_rows": 10,
                },
                "resources": {"jvm": {"generations": [{"pid": 9, "start_ticks": 90}]}},
                "samples_file": report_path.with_suffix(".jsonl").name,
            },
        )
        report_path.with_suffix(".jsonl").write_text("{}\n")
        envelope[key] = ref(case, report_path)
    write(case / "compactor-evidence.json", envelope)
    result["compactor_evidence"] = ref(case, case / "compactor-evidence.json")
    write(case / "result.json", result)
    return case, metadata


class CompactorEvidence(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(control, "ROOT", self.root))
        self.case, self.metadata = complete_fixture(self.root)

    def check(self):
        return cleanup.check_case(
            self.case, cleanup.fixture_stamp([(self.case, self.metadata)])
        )

    def mutate(self, key, change):
        envelope = json.loads((self.case / "compactor-evidence.json").read_text())
        path = self.case / envelope[key]["file"]
        value = json.loads(path.read_text())
        change(value)
        write(path, value)
        envelope[key] = ref(self.case, path)
        write(self.case / "compactor-evidence.json", envelope)
        result = json.loads((self.case / "result.json").read_text())
        result["compactor_evidence"] = ref(
            self.case, self.case / "compactor-evidence.json"
        )
        write(self.case / "result.json", result)

    def observation(self, phase, change, *, sync_operations=False):
        envelope = json.loads((self.case / "compactor-evidence.json").read_text())
        path = self.case / envelope["inspections"][phase]["file"]
        report = json.loads(path.read_text())
        change(report)
        write(path, report)
        if sync_operations:
            raw_path = path.parent / "catalog.json"
            raw = json.loads(raw_path.read_text())
            raw["load_table"]["metadata"]["snapshots"] = [
                {
                    "snapshot-id": row["snapshot_id"],
                    "summary": {"operation": row["operation"]},
                }
                for row in report["operations"]
            ]
            write(raw_path, raw)
        envelope["inspections"][phase] = ref(self.case, path)
        write(self.case / "compactor-evidence.json", envelope)
        result = json.loads((self.case / "result.json").read_text())
        result["compactor_evidence"] = ref(
            self.case, self.case / "compactor-evidence.json"
        )
        write(self.case / "result.json", result)

    def test_pinned_body_change_fails_even_with_well_formed_inspection(self):
        self.observation(
            "candidate",
            lambda value: value["pin"]["proof"].update(body_sha256="2" * 64),
        )
        with self.assertRaisesRegex(RuntimeError, "pinned snapshot or exact body"):
            self.check()

    def test_interrupted_new_replace_is_not_hidden_by_baseline_replace(self):
        self.observation(
            "interrupted",
            lambda value: value["operations"][-1].update(operation="replace"),
            sync_operations=True,
        )
        with self.assertRaisesRegex(RuntimeError, "replacement snapshot history"):
            self.check()

    def test_retained_old_metadata_cannot_hide_missing_pinned_append_snapshot(self):
        for phase in ("candidate", "interrupted", "recovered"):
            with self.subTest(phase=phase):
                envelope = json.loads(
                    (self.case / "compactor-evidence.json").read_text()
                )
                path = self.case / envelope["inspections"][phase]["file"]
                original = json.loads(path.read_text())["operations"]
                self.observation(
                    phase,
                    lambda value: value.update(
                        operations=[
                            row
                            for row in value["operations"]
                            if row["snapshot_id"] != 10
                        ]
                    ),
                    sync_operations=True,
                )
                # Keep pin bodies, old metadata URI, native current head and all
                # replacement IDs unchanged. Only the pinned append is lost.
                with self.assertRaisesRegex(RuntimeError, "pinned snapshot is absent"):
                    self.check()
                self.observation(
                    phase,
                    lambda value: value.update(operations=original),
                    sync_operations=True,
                )
                self.check()

    def test_recovery_must_add_a_new_replace(self):
        self.observation(
            "recovered",
            lambda value: value["operations"][-1].update(operation="append"),
            sync_operations=True,
        )
        with self.assertRaisesRegex(RuntimeError, "replacement snapshot history"):
            self.check()

    def test_candidate_parquet_and_body_row_counts_must_match(self):
        self.observation(
            "candidate", lambda value: value["candidate"]["parquet"].update(rows=9)
        )
        with self.assertRaisesRegex(RuntimeError, "complete Parquet"):
            self.check()

    def test_wrong_frozen_maintenance_source_fails(self):
        envelope = json.loads((self.case / "compactor-evidence.json").read_text())
        path = self.case / "maintenance" / envelope["fault_invocation"] / "started.json"
        value = json.loads(path.read_text())
        value["built_source"]["sources"] = {"iceberg-compaction/compact.py": "0" * 64}
        write(path, value)
        with self.assertRaisesRegex(RuntimeError, "maintenance source"):
            self.check()

    def test_full_compactor_case_preserves_truthful_kind_and_qualifies(self):
        self.assertEqual(self.check()["run_id"], RUN)
        self.assertEqual(
            next(iter(cleanup.cases_by_volume().values()))[0][1]["kind"],
            "iceberg-compactor-crash",
        )

    def test_multipart_xml_length_is_not_object_size_but_upload_part_rejects(self):
        self.check()
        self.mutate("gate_record", lambda row: row.update(operation="upload-part"))
        with self.assertRaisesRegex(RuntimeError, "acknowledgement hold"):
            self.check()

    def test_wrong_fault_native_state_rejects(self):
        self.mutate("fault_after", lambda row: row["state"].update(Pid=99))
        with self.assertRaisesRegex(RuntimeError, "fault"):
            self.check()

    def test_survivor_restart_rejects(self):
        self.mutate(
            "survivor_after",
            lambda row: row["query"]["state"].update(StartedAt="restarted"),
        )
        with self.assertRaisesRegex(RuntimeError, "survivor"):
            self.check()

    def test_late_actor_or_service_restart_cannot_pass(self):
        self.mutate(
            "survivor_final",
            lambda row: row["reader-000"]["state"].update(StartedAt="late-restart"),
        )
        with self.assertRaisesRegex(RuntimeError, "survivor"):
            self.check()

    def test_service_restart_during_final_verification_cannot_pass(self):
        self.mutate(
            "service_survivor_final",
            lambda row: row["storage"]["state"].update(StartedAt="late-restart"),
        )
        with self.assertRaisesRegex(RuntimeError, "survivor"):
            self.check()

    def test_unrelated_spark_configuration_or_version_cannot_qualify(self):
        self.mutate(
            "baseline_report",
            lambda row: row["compaction_result"].update(spark_version="different"),
        )
        with self.assertRaisesRegex(RuntimeError, "Spark maintenance"):
            self.check()

    def test_incomplete_maintenance_blocks_and_new_evidence_stales_receipts(self):
        stamp = cleanup.fixture_stamp([(self.case, self.metadata)])
        self.mutate(
            "survivor_after",
            lambda row: row["query"]["state"].update(StartedAt="changed"),
        )
        self.assertNotEqual(
            stamp["fingerprint"],
            cleanup.fixture_stamp([(self.case, self.metadata)])["fingerprint"],
        )
        next((self.case / "maintenance").glob("*/ended.json")).unlink()
        with self.assertRaises(FileNotFoundError):
            self.check()


class CommonIntegration(unittest.TestCase):
    def profile(self, case, auxiliary=True, mutate=None, cpus=11, native_version=None):
        images = {
            role: "runner" if role in control.GO_ROLES else role + "-image"
            for role in control.CAPS
        }
        services = {
            role: {
                "cpus": str(cpu),
                "mem_limit": str(memory),
                "memswap_limit": str(memory),
                "image": images[role],
                "networks": {"internal": {}},
                "environment": {
                    "WRITERS": "2",
                    "RECOVERY_READERS": "2",
                    "AWS_EC2_METADATA_DISABLED": "true",
                },
                "volumes": [
                    {"type": "bind", "source": str(case), "target": "/results"}
                ],
            }
            for role, (cpu, memory) in control.CAPS.items()
        }
        if mutate:
            mutate(services)

        def call(args, **kwargs):
            if "config" in args:
                return json.dumps(
                    {"services": services, "networks": {"internal": {"internal": True}}}
                )
            if args[:3] == ["docker", "image", "inspect"]:
                return json.dumps(
                    [
                        {
                            "Id": args[3],
                            "Config": {
                                "Labels": {
                                    "org.examples.owner": control.PROJECT,
                                    "org.examples.duckdb.version": native_version
                                    or control.DUCKDB_VERSION,
                                }
                            },
                        }
                    ]
                )
            if args[:3] == ["docker", "network", "inspect"]:
                return json.dumps(
                    [
                        {
                            "Id": "internal-id",
                            "Internal": True,
                            "Labels": {"com.docker.compose.project": control.PROJECT},
                        }
                    ]
                )
            if args[:2] == ["docker", "info"]:
                return json.dumps(
                    {"OSType": "linux", "NCPU": cpus, "MemTotal": 12 * control.GIB}
                )
            if args[:3] == ["docker", "ps", "-q"]:
                return ""
            raise AssertionError(args)

        with (
            patch.object(control, "DOCKER_ENV", {}),
            patch.object(control, "call", side_effect=call),
        ):
            return control.profile(
                case, container(), container("clickhouse"), auxiliary=auxiliary
            )

    def test_current_lane_rejects_historical_engine_image(self):
        with patch.object(control, "DUCKDB_VERSION", "2.0.1"):
            with self.assertRaisesRegex(RuntimeError, "selected DuckDB engine"):
                self.profile(control.ROOT / "results" / RUN, native_version="1.5.5")

    def test_auxiliary_profile_budget_caps_mount_and_images(self):
        case = control.ROOT / "results" / RUN
        self.assertEqual(self.profile(case)["live_budget"]["cpus"], 11)
        self.assertEqual(self.profile(case, auxiliary=False)["live_budget"]["cpus"], 7)
        with self.assertRaisesRegex(RuntimeError, "11 CPUs"):
            self.profile(case, cpus=10)
        with self.assertRaisesRegex(RuntimeError, "caps"):
            self.profile(
                case,
                mutate=lambda services: services["recovery-compactor"].update(
                    memswap_limit="9"
                ),
            )
        with self.assertRaisesRegex(RuntimeError, "witness mount"):
            self.profile(
                case,
                mutate=lambda services: services["compaction-upload-gate"]["volumes"][
                    0
                ].update(source="/foreign"),
            )

    def test_only_inspector_overlaps_live_unchanged_actors(self):
        value = container("recovery-writer")
        actor = Mock(service="recovery-writer", initial=value)
        inspection = {
            "RECOVERY_OBSERVATION_ID": OTHER,
            "RECOVERY_INSPECTION_PHASE": "before",
        }
        with (
            patch.object(control, "inspect_client", return_value=value),
            patch.object(
                control, "start_client", side_effect=RuntimeError("gated launch")
            ) as start,
        ):
            for command in ("recovery-seed", "recovery-verify"):
                with self.assertRaisesRegex(RuntimeError, "cannot overlap"):
                    control.tool(Path("unused"), command, {"writer": actor}, Mock(), {})
            start.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "gated launch"):
                control.tool(
                    Path("unused"),
                    "recovery-inspect",
                    {"writer": actor},
                    Mock(),
                    {},
                    inspection=inspection,
                )
        actor.service = "recovery-tools"
        with (
            patch.object(control, "inspect_client", return_value=value),
            patch.object(control, "start_client") as start,
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot overlap"):
                control.tool(
                    Path("unused"),
                    "recovery-inspect",
                    {"tool": actor},
                    Mock(),
                    {},
                    inspection=inspection,
                )
            start.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "inspection arguments"):
            control.tool(
                Path("unused"), "recovery-seed", {}, Mock(), {}, inspection=inspection
            )

    def test_live_gate_blocks_teardown_before_mutation(self):
        with (
            patch.object(cleanup, "cases_by_volume", return_value={}),
            patch.object(
                cleanup,
                "project_inventory",
                return_value={
                    "containers": [container("compaction-upload-gate")],
                    "volumes": [],
                    "networks": [],
                },
            ),
            patch.object(cleanup, "check_histories", return_value={}),
            patch.object(control, "call") as call,
        ):
            with self.assertRaisesRegex(RuntimeError, "live/unfenced"):
                cleanup.teardown("docker-down")
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
