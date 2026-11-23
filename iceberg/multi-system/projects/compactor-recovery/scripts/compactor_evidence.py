"""Offline gates for the isolated upload-held Spark compactor crash case."""

from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

import recovery_cleanup as cleanup

PHASES = ("before", "candidate", "interrupted", "recovered")
SURVIVORS = {"storage", "query", "writer-000", "writer-001", "reader-000", "reader-001"}


def reference(case, value, manifest):
    relative = Path(value["file"])
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("compactor evidence reference escaped case")
    path = cleanup.regular(case / relative, case)
    if cleanup.digest(path) != value["sha256"]:
        raise RuntimeError("compactor evidence bytes changed")
    cleanup.add_artifact(manifest, path, case)
    return path, cleanup.load(path, case)


def identity(value):
    return (
        value["id"],
        value["image_id"],
        value["restart_count"],
        value["state"]["Pid"],
        value["state"]["StartedAt"],
    )


def positive(value):
    return type(value) is int and value > 0


def body_proof(value):
    if (
        not positive(value["rows"])
        or not positive(value["batches"])
        or type(value["seed_rows"]) is not int
        or not 0 <= value["seed_rows"] <= value["rows"]
        or not re.fullmatch(r"[0-9a-f]{64}", value["body_sha256"])
    ):
        raise RuntimeError("invalid inspected complete-body proof")


def maintenance(case, metadata, evidence, manifest):
    roles = {
        "fault_invocation": "recovery-compactor",
        "recovery_invocation": "recovery-compactor",
        "gate_invocation": "compaction-upload-gate",
        "baseline_compactor_invocation": "recovery-compactor",
        "baseline_gate_invocation": "compaction-upload-gate",
    }
    directories, states = {}, {}
    images = cleanup.load(case / "profile.json", case)["images"]
    for key, service in roles.items():
        identifier = evidence[key]
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[0-9a-f]{32}", identifier)
            or uuid.UUID(hex=identifier).version != 4
        ):
            raise RuntimeError("invalid maintenance invocation")
        directory = cleanup.regular(case / "maintenance" / identifier, case)
        launch, started, ended = [
            cleanup.load(directory / name, case)
            for name in ("launch.json", "started.json", "ended.json")
        ]
        prefix = (
            "iceberg-compaction/"
            if service == "recovery-compactor"
            else "compaction-upload-gate/"
        )
        sources = {
            name: value
            for name, value in metadata["source_sha256"].items()
            if name.startswith(prefix)
        }
        if (
            not sources
            or launch["run_id"] != case.name
            or launch["invocation"] != identifier
            or launch["service"] != service
            or started["image_id"] != images[service]
            or started["built_source"]["sources"] != sources
            or ended["id"] != started["id"]
            or ended["image_id"] != started["image_id"]
            or ended["restart_count"] != started["restart_count"]
            or ended["state"]["StartedAt"] != started["state"]["StartedAt"]
            or not started["state"]["Running"]
            or started["state"]["Pid"] <= 0
            or ended["state"]["Running"]
            or ended["state"]["Pid"] != 0
            or ended["state"]["OOMKilled"]
        ):
            raise RuntimeError("maintenance source/image/identity/fencing differs")
        if key == "fault_invocation":
            if (
                ended["clean"] is not False
                or ended["state"]["ExitCode"] != 137
                or ended["aborted"]
            ):
                raise RuntimeError("expected compactor SIGKILL is not proved")
        elif (
            ended["clean"] is not True
            or ended["forced"]
            or ended["aborted"]
            or ended["state"]["ExitCode"] != 0
        ):
            raise RuntimeError("recovered compactor/gate did not stop cleanly")
        for path in directory.rglob("*"):
            if path.is_file():
                cleanup.add_artifact(manifest, path, case)
        directories[key], states[key] = directory, (started, ended)
    if (
        len(set(directories.values())) != len(roles)
        or {path.resolve() for path in (case / "maintenance").iterdir()}
        != {path.resolve() for path in directories.values()}
        or len({ended["id"] for _, ended in states.values()}) != len(roles)
    ):
        raise RuntimeError("maintenance invocation/file set differs")
    return states


def observations(case, config_sha, evidence, manifest):
    if set(evidence["inspections"]) != set(PHASES):
        raise RuntimeError("four distinct inspections required")
    reports, seen = {}, set()
    workload = case / ("recovery-" + case.name)
    tools = {}
    for directory in (case / "processes").iterdir():
        launch = cleanup.load(directory / "launch.json", case)
        if launch["command"] == "recovery-inspect":
            identifier = launch["extra"]["RECOVERY_OBSERVATION_ID"]
            if identifier in tools:
                raise RuntimeError("duplicate inspection client")
            tools[identifier] = launch
    for phase in PHASES:
        path, report = reference(case, evidence["inspections"][phase], manifest)
        identifier = cleanup.valid_uuid(report["observation_id"])
        if (
            identifier in seen
            or path != workload / "observations" / identifier / "inspection.json"
            or report["passed"] is not True
            or report.get("error")
            or report["phase"] != phase
            or report["run_id"] != case.name
            or report["config_sha256"] != config_sha
            or not positive(report["started_ns"])
            or report["ended_ns"] <= report["started_ns"]
            or tools[identifier]["extra"]["RECOVERY_INSPECTION_PHASE"] != phase
        ):
            raise RuntimeError("inspection identity/lifecycle differs")
        raw_path = path.parent / "catalog.json"
        raw = cleanup.load(raw_path, case)
        table = raw["load_table"]
        catalog = {
            "table_uuid": table["metadata"]["table-uuid"],
            "snapshot_id": table["metadata"]["current-snapshot-id"],
            "metadata_location": table["metadata-location"],
        }
        operations = [
            {
                "snapshot_id": row["snapshot-id"],
                "operation": row["summary"]["operation"],
            }
            for row in table["metadata"]["snapshots"]
        ]
        if (
            raw["available"] is not True
            or raw["run_id"] != case.name
            or raw["config_sha256"] != config_sha
            or not re.fullmatch(r"[0-9a-f]{64}", raw["body_sha256"])
            or table["metadata"]["format-version"] != 2
            or report["catalog"] != catalog
            or not positive(catalog["snapshot_id"])
            or report["operations"] != operations
            or not operations
            or any(not positive(row["snapshot_id"]) for row in operations)
            or len({row["snapshot_id"] for row in operations}) != len(operations)
            or catalog["snapshot_id"] not in {row["snapshot_id"] for row in operations}
        ):
            raise RuntimeError("inspection native catalog/snapshot history differs")
        if any(row["operation"] not in {"append", "replace"} for row in operations):
            raise RuntimeError(
                "unsupported snapshot operation in append-only compactor case"
            )
        body_proof(report["pin"]["proof"])
        cleanup.add_artifact(manifest, raw_path, case)
        reports[phase] = report
        seen.add(identifier)
    if set(tools) != seen:
        raise RuntimeError("inspection tool/report set differs")
    baseline = reports["before"]
    if (
        baseline["pin"]["observation_id"] != baseline["observation_id"]
        or baseline["pin"]["snapshot"] != baseline["catalog"]
    ):
        raise RuntimeError("baseline snapshot is not its own immutable pin")

    def replacements(report):
        return {
            row["snapshot_id"]
            for row in report["operations"]
            if row["operation"] == "replace"
        }

    baseline_replacements = replacements(baseline)
    if not baseline_replacements:
        raise RuntimeError("healthy baseline compactor did not publish replacement")
    for phase in PHASES[1:]:
        report = reports[phase]
        if baseline["pin"]["snapshot"]["snapshot_id"] not in {
            row["snapshot_id"] for row in report["operations"]
        }:
            raise RuntimeError("pinned snapshot is absent from current catalog history")
        if (
            not baseline_replacements <= replacements(report)
            or (phase != "recovered" and replacements(report) != baseline_replacements)
            or (
                phase == "recovered"
                and not replacements(report) - baseline_replacements
            )
        ):
            raise RuntimeError(
                "interrupted/recovered replacement snapshot history differs"
            )
        extra = tools[report["observation_id"]]["extra"]
        if (
            report["pin"] != baseline["pin"]
            or report["catalog"]["table_uuid"] != baseline["catalog"]["table_uuid"]
            or extra["RECOVERY_PIN_OBSERVATION"] != baseline["observation_id"]
        ):
            raise RuntimeError("pinned snapshot or exact body changed")
    if any(
        reports[left]["ended_ns"] >= reports[right]["started_ns"]
        for left, right in zip(PHASES, PHASES[1:])
    ):
        raise RuntimeError("inspection phase order differs")
    return reports


def check_compactor_case(case, metadata, result, manifest):
    path, evidence = reference(case, result["compactor_evidence"], manifest)
    workload = case / ("recovery-" + case.name)
    config = cleanup.load(workload / "config.json", case)
    config_sha = cleanup.digest(workload / "config.json")
    if (
        path != case / "compactor-evidence.json"
        or evidence["version"] != 1
        or evidence["run_id"] != case.name
        or evidence["config_sha256"] != config_sha
    ):
        raise RuntimeError("compactor evidence identity differs")
    states = maintenance(case, metadata, evidence, manifest)
    reports = observations(case, config_sha, evidence, manifest)
    baseline, candidate = reports["before"], reports["candidate"]["candidate"]
    if baseline["pin"]["proof"]["seed_rows"] != config["seed_rows"]:
        raise RuntimeError("baseline pin is missing seed rows")
    native_uuid = cleanup.valid_uuid(baseline["catalog"]["table_uuid"])
    prefix = (
        "s3://warehouse/lab/recovery_"
        + uuid.UUID(case.name).hex
        + "-"
        + native_uuid
        + "/data/"
    )
    if not baseline["catalog"]["metadata_location"].startswith(
        prefix.removesuffix("data/") + "metadata/"
    ):
        raise RuntimeError("baseline metadata is outside owned table")
    final_proof = cleanup.load(
        workload / "verifications" / result["verification_id"] / "verified.json", case
    )
    if final_proof["snapshot"]["table_uuid"] != native_uuid or not final_proof[
        "snapshot"
    ]["metadata_location"].startswith(prefix.removesuffix("data/") + "metadata/"):
        raise RuntimeError("final complete-body verification changed table identity")
    body_proof(candidate["proof"])
    uri, parquet = candidate["uri"], candidate["parquet"]
    parsed = urlsplit(uri)
    if (
        not uri.startswith(prefix)
        or not uri.endswith(".parquet")
        or parsed.query
        or parsed.fragment
        or parsed.netloc != "warehouse"
        or ".." in parsed.path.split("/")
        or parquet["path"] != uri
        or parquet["rows"] != candidate["proof"]["rows"]
        or parquet["manifest_record_count"] != parquet["rows"]
        or not positive(parquet["file_bytes"])
        or not positive(parquet["footer_bytes"])
        or parquet["footer_bytes"] >= parquet["file_bytes"]
        or not parquet["groups"]
    ):
        raise RuntimeError("candidate complete Parquet object is not proved")
    for group in parquet["groups"]:
        if (
            not group["compression"]
            or not group["encodings"]
            or any(
                not positive(group[key])
                for key in ("column_chunks", "compressed_bytes", "uncompressed_bytes")
            )
        ):
            raise RuntimeError("candidate Parquet column statistics invalid")
    _, gate = reference(case, evidence["gate_record"], manifest)
    _, status = reference(case, evidence["gate_status"], manifest)
    gate_config_path, gate_config = reference(case, evidence["gate_config"], manifest)
    _, baseline_config = reference(case, evidence["baseline_gate_config"], manifest)
    gate_config_sha = cleanup.digest(gate_config_path)
    expected_gate = (
        case
        / "maintenance"
        / evidence["gate_invocation"]
        / "gate"
        / ("upload-gate-" + case.name)
        / "config.json"
    )
    expected_baseline_gate = (
        case
        / "maintenance"
        / evidence["baseline_gate_invocation"]
        / "gate"
        / ("upload-gate-" + case.name)
        / "config.json"
    )
    if (
        gate_config_path != expected_gate
        or case / evidence["baseline_gate_config"]["file"] != expected_baseline_gate
    ):
        raise RuntimeError("gate configuration belongs to another invocation")
    if (
        gate_config["hold_final_acks"] is not True
        or baseline_config["hold_final_acks"] is not False
        or baseline_config["run_id"] != case.name
        or baseline_config["object_prefix"] != prefix
    ):
        raise RuntimeError("baseline/fault gate mode differs")
    if (
        gate_config["run_id"] != case.name
        or gate_config["object_prefix"] != prefix
        or gate["version"] != 1
        or gate["run_id"] != case.name
        or gate["config_sha256"] != gate_config_sha
        or gate["object_prefix"] != prefix
        or gate["object_uri"] != uri
        or gate["event"] != "backend-complete-ack-held"
        or gate["ack_held"] is not True
        or not 200 <= gate["backend_status"] < 300
        or (gate["method"], gate["operation"])
        not in {("PUT", "put-object"), ("POST", "complete-multipart-upload")}
        or not positive(gate["backend_completed_ns"])
        or gate["backend_completed_ns"] > reports["candidate"]["started_ns"]
        or status["version"] != 1
        or status["hold_final_acks"] is not True
        or status["fatal_error"]
        or status["stopping"] is not False
        or status["run_id"] != case.name
        or status["config_sha256"] != gate_config_sha
        or status["object_prefix"] != prefix
        or gate not in status["active_holds"]
    ):
        raise RuntimeError(
            "durable successful upload acknowledgement hold is not proved"
        )
    gate_path = case / evidence["gate_record"]["file"]
    journal = Path(gate["journal_file"])
    if (
        journal.is_absolute()
        or ".." in journal.parts
        or cleanup.regular(gate_config_path.parent.parent / journal, case) != gate_path
    ):
        raise RuntimeError("gate journal path differs")
    cleanup.add_artifact(manifest, gate_config_path, case)
    baseline_path, forwarded = reference(
        case, evidence["baseline_gate_record"], manifest
    )
    baseline_journal = Path(forwarded["journal_file"])
    if (
        forwarded["version"] != 1
        or forwarded["run_id"] != case.name
        or forwarded["config_sha256"] != evidence["baseline_gate_config"]["sha256"]
        or forwarded["object_prefix"] != prefix
        or not forwarded["object_uri"].startswith(prefix)
        or forwarded["event"] != "backend-complete-ack-forwarded"
        or forwarded["ack_held"] is not False
        or not 200 <= forwarded["backend_status"] < 300
        or (forwarded["method"], forwarded["operation"])
        not in {("PUT", "put-object"), ("POST", "complete-multipart-upload")}
        or not positive(forwarded["backend_completed_ns"])
        or forwarded["backend_completed_ns"] >= reports["before"]["started_ns"]
        or baseline_journal.is_absolute()
        or ".." in baseline_journal.parts
        or cleanup.regular(
            expected_baseline_gate.parent.parent / baseline_journal, case
        )
        != baseline_path
    ):
        raise RuntimeError("healthy baseline final-upload forwarding is not proved")
    candidate_tools = [
        cleanup.load(directory / "launch.json", case)
        for directory in (case / "processes").iterdir()
    ]
    candidate_tool = next(
        value
        for value in candidate_tools
        if value.get("extra", {}).get("RECOVERY_OBSERVATION_ID")
        == reports["candidate"]["observation_id"]
    )
    if candidate_tool["extra"]["RECOVERY_CANDIDATE_URI"] != uri:
        raise RuntimeError("candidate inspection client URI differs")
    before_path, before = reference(case, evidence["fault_before"], manifest)
    after_path, after = reference(case, evidence["fault_after"], manifest)
    fault_started, fault_ended = states["fault_invocation"]
    state_keys = ("id", "image_id", "state", "restart_count")
    if (
        before_path == after_path
        or identity(before) != identity(fault_started)
        or not before["state"]["Running"]
        or after != {key: fault_ended[key] for key in state_keys}
        or after["state"]["Running"]
        or after["state"]["Pid"] != 0
        or after["state"]["ExitCode"] != 137
        or after["state"]["OOMKilled"]
        or not reports["candidate"]["ended_ns"]
        <= evidence["fault_started_ns"]
        <= evidence["fault_completed_ns"]
        <= reports["interrupted"]["started_ns"]
    ):
        raise RuntimeError("exact held-upload compactor fault is not proved")
    survivor_before_path, survivor_before = reference(
        case, evidence["survivor_before"], manifest
    )
    survivor_after_path, survivor_after = reference(
        case, evidence["survivor_after"], manifest
    )
    survivor_final_path, survivor_final = reference(
        case, evidence["survivor_final"], manifest
    )
    service_final_path, service_final = reference(
        case, evidence["service_survivor_final"], manifest
    )
    if (
        len(
            {
                survivor_before_path,
                survivor_after_path,
                survivor_final_path,
                service_final_path,
            }
        )
        != 4
        or set(survivor_before) != SURVIVORS
        or set(survivor_after) != SURVIVORS
        or set(survivor_final) != SURVIVORS
        or set(service_final) != {"storage", "query"}
    ):
        raise RuntimeError("all six survivor observations required")
    original = {"storage": metadata["storage"], "query": metadata["query"]}
    for directory in (case / "processes").iterdir():
        launch = cleanup.load(directory / "launch.json", case)
        if launch["role"] in SURVIVORS:
            original[launch["role"]] = cleanup.load(directory / "started.json", case)
    for role in SURVIVORS:
        values = [survivor_before[role], survivor_after[role], survivor_final[role]]
        if role in service_final:
            values.append(service_final[role])
        for value in values:
            if (
                identity(value) != identity(original[role])
                or not value["state"]["Running"]
                or value["state"]["OOMKilled"]
            ):
                raise RuntimeError("unrelated survivor exited/restarted")
    expected_versions = metadata.get(
        "maintenance_versions", {"spark_version": "4.0.1", "iceberg_version": "1.10.1"}
    )
    expected_profile = metadata.get("lab_profile", "historical")
    declared_versions = (
        {"spark_version": "4.1.4", "iceberg_version": "1.12.0"}
        if expected_profile == "current"
        else {"spark_version": "4.0.1", "iceberg_version": "1.10.1"}
    )
    if (
        expected_profile not in {"historical", "current"}
        or expected_versions != declared_versions
    ):
        raise RuntimeError(
            "maintenance release declaration is not a pinned lab profile"
        )
    for report_key in ("baseline_report", "recovered_report"):
        report_path, recovered = reference(case, evidence[report_key], manifest)
        compacted = recovered["compaction_result"]
        expected_invocation = evidence[
            "baseline_compactor_invocation"
            if report_key == "baseline_report"
            else "recovery_invocation"
        ]
        expected_endpoint = (
            "http://compaction-upload-gate:8333"
            if report_key == "baseline_report"
            else "http://storage:8333"
        )
        effective = compacted["configuration"]
        observation = reports[
            "before" if report_key == "baseline_report" else "recovered"
        ]
        snapshots = {row["snapshot_id"] for row in observation["operations"]}
        if (
            recovered["passed"] is not True
            or recovered["errors"]
            or recovered["stop"]["attempted"] is not True
            or recovered["stop"]["passed"] is not True
            or report_path.parent
            != case / "maintenance" / expected_invocation / "spark"
            or compacted["spark_version"] != expected_versions["spark_version"]
            or compacted["iceberg_version"] != expected_versions["iceberg_version"]
            or recovered["configuration"] != effective
            or effective["live_writers"] is not True
            or effective["rewrite_manifests"] is not False
            or effective["s3_endpoint"] != expected_endpoint
            or effective["catalog_url"] != "http://storage:8181"
            or effective["rewrite_options"]
            != {
                "partial-progress.enabled": "false",
                "max-concurrent-file-group-rewrites": "1",
            }
            or compacted["table"] != "lab." + config["table"]
            or compacted["retained_snapshot_rows"] != compacted["before"]["rows"]
            or compacted["after"]["rows"] < compacted["before"]["rows"]
            or compacted["before"]["snapshot"] not in snapshots
            or compacted["after"]["snapshot"] not in snapshots
            or not recovered["resources"]["jvm"]["generations"]
        ):
            raise RuntimeError(
                "recovered Spark maintenance/resource report did not qualify"
            )
        samples = cleanup.regular(report_path.parent / recovered["samples_file"], case)
        cleanup.add_artifact(manifest, samples, case)
    return evidence
