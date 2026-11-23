"""Fail-closed retention and explicit re-verification for this local Iceberg lab."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import time
from urllib.parse import quote
import uuid

import recovery_control as control
from recovery_resources import ResourceSampler

VOLUME_FIELDS = ("Name", "CreatedAt", "Mountpoint")
PROFILES = [
    "--profile",
    "cluster",
    "--profile",
    "tools",
    "--profile",
    "production",
    "--profile",
    "dependency-fault",
]
SERVICES = {
    "storage",
    "clickhouse",
    "runner",
    "compactor",
    "ch-workload",
    "recovery-tools",
    "recovery-writer",
    "recovery-reader",
    "recovery-compactor",
    "compaction-upload-gate",
    "dependency-fault",
    *[f"ch{s}{r}" for s in range(1, 4) for r in range(1, 4)],
    "keeper1",
    "keeper2",
    "keeper3",
}
IMAGES = (
    "iceberg-multi-runner:dev",
    "iceberg-multi-runner-duckdb2:dev",
    "iceberg-multi-compactor:dev",
    "iceberg-multi-ch-workload:dev",
    "iceberg-multi-upload-gate:dev",
    "iceberg-multi-dependency-fault:dev",
)
if control.lab.PROFILE == "current":
    IMAGES = (
        "iceberg-multi-current-runner-duckdb2:dev",
        "iceberg-multi-current-runner-duckdb2-http:dev",
        "iceberg-multi-compactor-current:dev",
        "iceberg-multi-current-ch-workload:dev",
        "iceberg-multi-current-upload-gate:dev",
        "iceberg-multi-current-dependency-fault:dev",
    )
CASE_KINDS = {"iceberg-storage-crash", "iceberg-compactor-crash"}
if control.lab.VARIANT == "http":
    # Builds are shared immutable cache across these lanes; this lane owns only
    # its project resources, never an image still referenced by retained cases.
    IMAGES = ()
PREFAULT_ARCHIVE_ID = "f1b2af24-f6c2-4bd2-8e44-a35392666f23"
COMPOUND_ARCHIVE_ID = "2b463a19-1a8c-43bf-b84d-5a0fa03f5836"


def regular(path, root):
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root):
        raise RuntimeError("evidence escaped owned directory")
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise RuntimeError("symlinked evidence is not accepted")
        if part == root:
            break
    return path


def digest(path):
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("missing/non-regular evidence file: " + str(path))
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path, root):
    return json.loads(regular(path, root).read_text())


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        raise RuntimeError("invalid recovery UUID") from error
    if parsed.version != 4 or str(parsed) != value:
        raise RuntimeError("explicit lowercase version-4 UUID required")
    return value


def volume_key(value):
    if not all(isinstance(value.get(key), str) and value[key] for key in VOLUME_FIELDS):
        raise RuntimeError("incomplete volume identity")
    return tuple(value[key] for key in VOLUME_FIELDS)


def results_root():
    results = control.ROOT / control.lab.RESULTS_SUBDIR
    if (control.ROOT / "results").is_symlink() or results.is_symlink():
        raise RuntimeError("results must be an owned directory, not a symlink")
    return results


def index_records():
    results = results_root()
    path = results / "fixture-index.jsonl"
    if not path.exists():
        return []
    regular(path, results)
    body = path.read_bytes()
    if body and not body.endswith(b"\n"):
        raise RuntimeError("torn fixture index; preserve it")
    records, seen = [], set()
    for line in body.splitlines():
        value = json.loads(line)
        identifier = valid_uuid(value["run_id"])
        if value["version"] != 1 or value["kind"] != "register" or identifier in seen:
            raise RuntimeError("invalid/duplicate fixture index entry")
        if type(value["registered_ns"]) is not int or value["registered_ns"] <= 0:
            raise RuntimeError("invalid fixture registration time")
        volume_key(value["volume"])
        path = Path(value["case"])
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.name != identifier
            or path.parent.name != "production"
        ):
            raise RuntimeError("invalid indexed case path")
        control.lab.validate_results(control.ROOT / path, control.ROOT)
        regular(control.ROOT / path, results)
        seen.add(identifier)
        records.append(value)
    return records


def register_fixture(case, metadata=None):
    """Filesystem only; caller holds controller.lock, before seed or another mutation."""
    root, results = control.ROOT.resolve(), results_root()
    case = regular(Path(case).absolute(), results).resolve()
    control.lab.validate_results(case, control.ROOT)
    metadata = metadata or load(case / "case.json", results)
    if (
        metadata.get("run_id") != valid_uuid(case.name)
        or metadata.get("project") != control.PROJECT
        or metadata.get("kind") not in CASE_KINDS
    ):
        raise RuntimeError("fixture registration identity differs")
    records = index_records()
    if any(record["run_id"] == case.name for record in records):
        raise RuntimeError("fixture UUID already registered; never reseed")
    record = {
        "version": 1,
        "kind": "register",
        "run_id": case.name,
        "case": str(case.relative_to(root)),
        "case_sha256": digest(case / "case.json"),
        "volume": {key: metadata["volume"][key] for key in VOLUME_FIELDS},
        "registered_ns": time.time_ns(),
    }
    volume_key(record["volume"])
    descriptor = os.open(
        results / "fixture-index.jsonl",
        os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW,
        0o644,
    )
    with os.fdopen(descriptor, "a") as stream:
        stream.write(json.dumps(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    control.sync_directory(results)


def cases_by_volume():
    results, groups = results_root(), {}
    records = index_records()
    indexed = set()
    for record in records:
        case = regular(control.ROOT / record["case"], results)
        metadata = load(case / "case.json", case)
        if (
            metadata["kind"] not in CASE_KINDS
            or metadata["project"] != control.PROJECT
            or metadata["run_id"] != case.name
            or digest(case / "case.json") != record["case_sha256"]
            or volume_key(metadata["volume"]) != volume_key(record["volume"])
        ):
            raise RuntimeError("indexed case identity/metadata changed")
        indexed.add(case.resolve())
        groups.setdefault(volume_key(metadata["volume"]), []).append((case, metadata))
    actual = {
        regular(path, results).resolve()
        for path in results.glob("**/production/*")
        if path.is_dir()
        and not (
            control.lab.PROFILE == "historical"
            and path.is_relative_to(control.ROOT / "results/current")
        )
    }
    if actual != indexed:
        raise RuntimeError("missing or unregistered recovery case; retain storage")
    return groups


def witness_names(config):
    if config["writers"] != 2 or config["readers"] != 2:
        raise RuntimeError("unexpected actor topology in fixture")
    actors = [
        f"{kind}-{number:03d}" for kind in ("writer", "reader") for number in range(2)
    ]
    journals = {
        "bootstrap.jsonl",
        "seed.jsonl",
        "seed-checks.jsonl",
        *(actor + ".jsonl" for actor in actors),
    }
    return actors, journals | {"config.json", "seed-complete.json"}


def event_time(path):
    # Timestamp is a freshness bound, not the correctness oracle. Raw bytes also enter the fingerprint.
    try:
        values = (
            control.history(path)
            if path.suffix == ".jsonl"
            else [json.loads(path.read_text())]
        )
    except (ValueError, UnicodeError):
        return 0  # A malformed witness will separately fail qualification/hash checks.
    return max(
        (
            value[key]
            for value in values
            if isinstance(value, dict)
            for key in ("started_ns", "ended_ns", "utc_ns", "created_ns")
            if type(value.get(key)) is int
        ),
        default=0,
    )


def fixture_stamp(cases):
    hashes, latest = {}, 0
    registrations = {value["run_id"]: value for value in index_records()}
    for case, metadata in cases:
        registered = json.dumps(
            registrations[case.name], sort_keys=True, separators=(",", ":")
        ).encode()
        hashes["@registration:" + case.name] = hashlib.sha256(registered).hexdigest()
        latest = max(latest, metadata["created_ns"])
        if case.name == COMPOUND_ARCHIVE_ID:
            from compound_archive import fingerprint

            failed = fingerprint(case)
            hashes.update(failed["files"])
            latest = max(latest, failed["latest_operation_ns"])
            continue
        paths = [case / "case.json"]
        workload = case / ("recovery-" + case.name)
        paths += sorted(workload.glob("*.json")) + sorted(workload.glob("*.jsonl"))
        for name in (
            "before-kill.json",
            "after-kill.json",
            "reopened.json",
            "phases.jsonl",
        ):
            if (case / name).exists():
                paths.append(case / name)
        if metadata["kind"] == "iceberg-compactor-crash":
            if case.name != PREFAULT_ARCHIVE_ID:
                paths.append(case / "compactor-evidence.json")
            for directory in (case / "maintenance", workload / "observations"):
                paths += sorted(path for path in directory.rglob("*") if path.is_file())
        for path in paths:
            regular(path, case)
            hashes[str(path.relative_to(control.ROOT))] = digest(path)
            if path.name == "phases.jsonl":
                latest = max(
                    [
                        latest,
                        *(
                            row["host_ns"]
                            for row in control.history(path)
                            if row["phase"] in {"fault", "recovery"}
                        ),
                    ],
                )
            else:
                latest = max(latest, event_time(path))
    body = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return {
        "fingerprint": hashlib.sha256(body).hexdigest(),
        "latest_operation_ns": latest,
        "last_case": cases[-1][0].name,
    }


def add_artifact(manifest, path, case):
    regular(path, case)
    manifest[str(path.relative_to(case))] = digest(path)


def check_clients(
    case,
    result,
    metadata,
    manifest,
    *,
    allow_failed_verifiers=False,
    artifact_cutoff_ns=None,
):
    roles, tools = set(), []
    images = load(case / "profile.json", case)["images"]
    for directory in sorted((case / "processes").iterdir()):
        regular(directory, case)
        launch, started, ended = [
            load(directory / name, case)
            for name in ("launch.json", "started.json", "ended.json")
        ]
        if launch["run_id"] != case.name or launch["invocation"] != directory.name:
            raise RuntimeError("external client invocation identity changed")
        service, role = launch["service"], launch["role"]
        if service not in {"recovery-tools", "recovery-writer", "recovery-reader"}:
            raise RuntimeError("unrecognized recovery client role")
        original = launch["created_host_ns"] <= result["completed_ns"]
        if not original and (
            service != "recovery-tools" or launch["command"] != "recovery-verify"
        ):
            raise RuntimeError(
                "post-result mutation actor is not an authorized verification"
            )
        if (
            ended["id"] != started["id"]
            or ended["image_id"] != started["image_id"]
            or ended["state"]["Running"]
            or ended["state"]["Pid"] != 0
        ):
            raise RuntimeError("external client lacks matching positive fencing")
        failed_verifier = (
            allow_failed_verifiers
            and service == "recovery-tools"
            and launch["command"] == "recovery-verify"
            and ended["state"]["ExitCode"] == 1
            and ended["clean"] is False
        )
        if original and (
            (ended["clean"] is not True and not failed_verifier)
            or ended["forced"]
            or ended["aborted"]
            or (ended["state"]["ExitCode"] != 0 and not failed_verifier)
            or ended["state"]["OOMKilled"]
            or ended["state"]["StartedAt"] != started["state"]["StartedAt"]
            or ended["restart_count"] != started["restart_count"]
        ):
            raise RuntimeError("original client did not end cleanly")
        if original and started["image_id"] != images[service]:
            raise RuntimeError("original client image differs from frozen profile")
        if original and service == "recovery-tools":
            tools.append(launch["command"])
        if service != "recovery-tools":
            if role in roles:
                raise RuntimeError("duplicate original actor")
            roles.add(role)
        expected_sources = {
            Path(name).name: checksum
            for name, checksum in metadata["source_sha256"].items()
            if name.startswith("app/")
        }
        if original and started["built_source"]["sources"] != expected_sources:
            raise RuntimeError("original client build sources differ from frozen case")
        if (
            artifact_cutoff_ns is None
            or launch["created_host_ns"] <= artifact_cutoff_ns
        ):
            for path in directory.rglob("*"):
                if path.is_file():
                    add_artifact(manifest, path, case)
    if roles != {"writer-000", "writer-001", "reader-000", "reader-001"}:
        raise RuntimeError("missing external actor lifecycle proof")
    expected_tools = ["recovery-seed", "recovery-verify"] + (
        ["recovery-verify"] if allow_failed_verifiers else []
    )
    if metadata["kind"] == "iceberg-compactor-crash":
        expected_tools += ["recovery-inspect"] * 4
    if sorted(tools) != sorted(expected_tools):
        raise RuntimeError("missing original seed/verification client proof")


def check_native_versions(directory, proof, storage, metadata, manifest, case):
    reference = proof.get("native_versions")
    if "native_versions" not in proof and "native_versions" not in storage:
        if "duckdb_version" not in metadata:
            return  # Historical proofs predate explicit native-version evidence.
        raise RuntimeError("native version evidence is required")
    if (
        not isinstance(reference, dict)
        or set(reference) != {"file", "sha256"}
        or reference != storage.get("native_versions")
        or reference["file"] != "native-versions.json"
    ):
        raise RuntimeError("native version references differ")
    path = regular(directory / "native-versions.json", case)
    if digest(path) != reference["sha256"]:
        raise RuntimeError("native version evidence changed")
    versions = load(path, case)
    bindings = {"v2.0.1": "v2.20000.0", "v1.5.5": "v2.10505.0"}
    engine, platform = versions.get("engine"), versions.get("platform")

    def matches(value, pattern):
        return isinstance(value, str) and re.fullmatch(pattern, value)

    if (
        not isinstance(engine, str)
        or engine not in bindings
        or versions.get("duckdb_go_version") != bindings[engine]
        or not matches(platform, r"[a-z0-9_]+")
        or not matches(versions.get("lab_binary_sha256"), r"[0-9a-f]{64}")
    ):
        raise RuntimeError("invalid native engine/binding identity")
    extensions = versions.get("extensions")
    if (
        not isinstance(extensions, list)
        or len(extensions) != 3
        or any(not isinstance(item, dict) for item in extensions)
        or any(
            item.get("name") not in ("avro", "httpfs", "iceberg") for item in extensions
        )
        or sorted(item.get("name", "") for item in extensions)
        != ["avro", "httpfs", "iceberg"]
    ):
        raise RuntimeError("invalid native extension set")
    prefix = f"/opt/duckdb/extensions/{engine}/{platform}/"
    for item in extensions:
        if (
            not matches(item.get("version"), r"[0-9a-f]+")
            or not matches(item.get("sha256"), r"[0-9a-f]{64}")
            or item.get("installed") is not True
            or type(item.get("loaded")) is not bool
            or item.get("install_path") != prefix + item["name"] + ".duckdb_extension"
        ):
            raise RuntimeError("invalid native extension identity/path")
    add_artifact(manifest, path, case)


def check_verification(case, reference, metadata, manifest, newest_ns):
    workload = case / ("recovery-" + case.name)
    identifier = valid_uuid(reference["verification_id"])
    directory = regular(workload / "verifications" / identifier, case)
    proof = load(directory / "verified.json", case)
    if digest(directory / "verified.json") != reference["verification_sha256"]:
        raise RuntimeError("verification evidence changed")
    if (
        not all(
            proof[key] is True
            for key in (
                "passed",
                "qualified",
                "integrity_passed",
                "actor_lifecycle_passed",
                "strict_mutation_history_passed",
                "reader_contract_passed",
            )
        )
        or proof["diagnostic"]
        or proof["qualification_issues"]
    ):
        raise RuntimeError(
            "failed or diagnostic verification cannot authorize teardown"
        )
    config = load(workload / "config.json", case)
    checksum = digest(workload / "config.json")
    if (
        config["version"] != 1
        or config["run_id"] != case.name
        or config["table"] != "recovery_" + uuid.UUID(case.name).hex
        or proof["run_id"] != case.name
        or proof["verification_id"] != identifier
        or proof["config_sha256"] != checksum
    ):
        raise RuntimeError("verification/config identity mismatch")
    actors, expected = witness_names(config)
    actual = {path.name for path in workload.glob("*.jsonl")} | {
        "config.json",
        "seed-complete.json",
    }
    if actual != expected or set(proof["history_sha256"]) != expected:
        raise RuntimeError("witness file set changed")
    for name in expected:
        path = workload / name
        if digest(regular(path, case)) != proof["history_sha256"][name]:
            raise RuntimeError("witness bytes changed: " + name)
        add_artifact(manifest, path, case)
    seed = load(workload / "seed-complete.json", case)
    if (
        seed["run_id"] != case.name
        or seed["config_sha256"] != checksum
        or seed["rows"] != config["seed_rows"]
    ):
        raise RuntimeError("seed completion identity differs")
    if {path.name for path in workload.glob("*-ended.json")} != {
        actor + "-ended.json" for actor in actors
    }:
        raise RuntimeError("actor completion file set changed")
    for actor in actors:
        path = workload / (actor + "-ended.json")
        ended = load(path, case)
        if (
            ended["run_id"] != case.name
            or ended["config_sha256"] != checksum
            or ended["clean_shutdown"] is not True
        ):
            raise RuntimeError("actor completion identity/clean shutdown differs")
        if actor.startswith("reader") and ended["engine"] != (
            "duckdb" if actor == "reader-000" else "clickhouse"
        ):
            raise RuntimeError("reader completion engine differs")
        add_artifact(manifest, path, case)
    invocation = load(directory / "invocation.json", case)
    if (
        invocation["run_id"] != case.name
        or invocation["verification_id"] != identifier
        or invocation["config_sha256"] != checksum
        or invocation["diagnostic"]
    ):
        raise RuntimeError("verification invocation identity differs")
    if proof["utc_ns"] < newest_ns:
        raise RuntimeError("stale verification predates latest fixture operation")
    if set(proof["exports"]) != {"duckdb", "clickhouse"}:
        raise RuntimeError("both engines require complete exports")
    for engine, exported in proof["exports"].items():
        if (
            exported["run_id"] != case.name
            or exported["verification_id"] != identifier
            or exported["config_sha256"] != checksum
            or exported["csv_file"] != engine + "-rows.csv"
        ):
            raise RuntimeError("export identity differs")
        engine_proof = directory / (engine + "-proof.json")
        if (
            load(engine_proof, case) != exported
            or digest(directory / exported["csv_file"]) != exported["csv_sha256"]
        ):
            raise RuntimeError("full-body CSV/proof changed")
        add_artifact(manifest, engine_proof, case)
        add_artifact(manifest, directory / exported["csv_file"], case)
    if proof["exports"]["duckdb"]["proof"] != proof["exports"]["clickhouse"]["proof"]:
        raise RuntimeError("full-engine body proofs differ")
    storage_ref = proof["storage_metrics"]
    storage_path = directory / "storage-metrics.json"
    if (
        storage_ref["file"] != storage_path.name
        or storage_ref["snapshot"] != proof["snapshot"]
        or digest(regular(storage_path, case)) != storage_ref["sha256"]
    ):
        raise RuntimeError("native storage measurement evidence changed")
    storage = load(storage_path, case)
    if (
        storage["passed"] is not True
        or storage["run_id"] != case.name
        or storage["verification_id"] != identifier
        or storage["config_sha256"] != checksum
        or storage["snapshot"] != proof["snapshot"]
    ):
        raise RuntimeError("native storage measurement identity differs")
    check_native_versions(directory, proof, storage, metadata, manifest, case)
    totals = storage["totals"]
    ratio = storage["encoded_parquet_chunk_compression_ratio"]
    if (
        any(
            type(totals[key]) is not int or totals[key] <= 0
            for key in (
                "files",
                "rows",
                "file_bytes",
                "column_chunks",
                "compressed_chunk_bytes",
                "uncompressed_chunk_bytes",
            )
        )
        or totals["files"] != len(storage["files"])
        or totals["rows"] != proof["exports"]["duckdb"]["proof"]["rows_verified"]
        or not isinstance(ratio, (int, float))
        or not math.isfinite(ratio)
        or not math.isclose(
            ratio,
            totals["uncompressed_chunk_bytes"] / totals["compressed_chunk_bytes"],
            rel_tol=1e-12,
        )
    ):
        raise RuntimeError("invalid native storage measurement totals/ratio")
    add_artifact(manifest, storage_path, case)
    for path in (directory / "verified.json", directory / "invocation.json"):
        add_artifact(manifest, path, case)
    return proof


def base_record(case):
    if case.name == PREFAULT_ARCHIVE_ID:
        from prefault_archive import archived_base

        return archived_base(case)
    from recovery_retirement import RUN_ID, archived_base

    if case.name == RUN_ID or any(
        (case / name).exists() for name in ("failure.json", "cleanup-errors.json")
    ):
        return archived_base(case)
    result_path = case / "result.json"
    result = load(result_path, case)
    if result["passed"] is not True:
        raise RuntimeError("unqualified recovery case")
    return result, result_path, {}


def base_artifacts(case):
    if case.name == PREFAULT_ARCHIVE_ID:
        # This sealed failed baseline never reached its compactor-fault workflow.
        # Its dedicated archive validates its actual actors and data audit instead.
        return base_record(case)
    result, result_path, manifest = base_record(case)
    metadata = load(case / "case.json", case)
    for name in (
        "case.json",
        "profile.json",
        "resource-summary.json",
        "phase-resources.json",
        "operation-summary.json",
    ):
        add_artifact(manifest, case / name, case)
    add_artifact(manifest, result_path, case)
    check_clients(
        case,
        result,
        metadata,
        manifest,
        allow_failed_verifiers=result.get("status") == "failed-but-archived",
    )
    check_verification(case, result, metadata, {}, 0)
    if metadata["kind"] == "iceberg-compactor-crash":
        from compactor_evidence import check_compactor_case

        check_compactor_case(case, metadata, result, manifest)
    return result, result_path, manifest


def check_case(case, stamp, *, require_fresh=True, ignore_receipts=False):
    if case.name == COMPOUND_ARCHIVE_ID:
        from compound_archive import preserved_case

        return preserved_case(case, stamp, require_fresh=require_fresh)
    metadata = load(case / "case.json", case)
    result, result_path, manifest = base_artifacts(case)
    selected, receipt = result, None
    attempts = []
    for directory in [] if ignore_receipts else (case / "reverifications").glob("*"):
        invocation = load(directory / "invocation.json", case)
        if invocation["run_id"] != case.name or invocation[
            "verification_id"
        ] != valid_uuid(directory.name):
            raise RuntimeError("reverification invocation identity differs")
        attempts.append((invocation["started_ns"], directory, invocation))
    if attempts:
        _, directory, invocation = max(attempts)
        receipt = load(directory / "receipt.json", case)
        if (
            receipt.get("passed") is not True
            or receipt["run_id"] != case.name
            or receipt["verification_id"] != directory.name
            or receipt["invocation"] != invocation
            or volume_key(receipt["volume"]) != volume_key(metadata["volume"])
            or receipt["case_sha256"] != digest(case / "case.json")
            or receipt["base_result_sha256"] != digest(result_path)
        ):
            raise RuntimeError("reverification receipt identity or verdict differs")
        if require_fresh and receipt["fixture"] != stamp:
            raise RuntimeError("stale reverification fixture fingerprint")
        selected = receipt
    elif require_fresh and stamp["last_case"] != case.name:
        raise RuntimeError(
            "earlier UUID requires explicit verify-history after later cases"
        )
    proof = check_verification(
        case,
        selected,
        metadata,
        manifest,
        stamp["latest_operation_ns"] if require_fresh else 0,
    )
    if receipt is not None:
        add_artifact(manifest, directory / "invocation.json", case)
        add_artifact(manifest, directory / "resources.json", case)
    if receipt is not None and receipt["artifacts"] != manifest:
        raise RuntimeError("reverification artifact manifest changed")
    if receipt is not None:
        add_artifact(manifest, directory / "receipt.json", case)
    return {
        "case": str(case.relative_to(control.ROOT)),
        "run_id": case.name,
        "verification_id": selected["verification_id"],
        "verification_sha256": selected["verification_sha256"],
        "table_uuid": proof["snapshot"]["table_uuid"],
        "artifacts": manifest,
        **(
            {"original_status": "failed", "data_preservation_passed": True}
            if result.get("status") == "failed-but-archived"
            else {}
        ),
    }


def project_inventory():
    containers = [
        control.owned(identifier)
        for identifier in control.call(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + control.PROJECT,
            ]
        ).split()
    ]
    for value in containers:
        if value["Config"]["Labels"]["com.docker.compose.service"] not in SERVICES:
            raise RuntimeError("unknown owned service; preserve resources")
    volumes = []
    for name in control.call(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + control.PROJECT,
        ]
    ).split():
        (value,) = json.loads(control.call(["docker", "volume", "inspect", name]))
        volumes.append(value)
    networks = []
    for identifier in control.call(
        [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + control.PROJECT,
        ]
    ).split():
        (value,) = json.loads(
            control.call(["docker", "network", "inspect", identifier])
        )
        networks.append(value)
    return {"containers": containers, "volumes": volumes, "networks": networks}


def current_volume(inventory):
    candidates = [
        value
        for value in inventory["volumes"]
        if value.get("Labels", {}).get("com.docker.compose.volume") == "storage"
    ]
    if len(candidates) > 1:
        raise RuntimeError("ambiguous current storage fixture")
    return volume_key(candidates[0]) if candidates else None


def catalog_recoveries(storage, *, snapshots=False):
    """Explicit lab OAuth over container-local HTTP; curl never follows redirects."""

    def request(path, *, token=None, form=None):
        limit = 65536 if form else 8 * 1024**2
        arguments = [
            "docker",
            "exec",
            storage["Id"],
            "curl",
            "-q",
            "--noproxy",
            "*",
            "--proto",
            "=http",
            "--connect-timeout",
            "5",
            "--max-time",
            "10",
            "--max-filesize",
            str(limit),
            "--fail",
            "--silent",
            "--show-error",
            "--write-out",
            "\n%{http_code}",
        ]
        if token:
            arguments += ["--header", "Authorization: Bearer " + token]
        if form:
            arguments += ["--data", form]
        output = control.call(
            arguments + ["http://127.0.0.1:8181/v1/" + path], timeout=15
        )
        body, separator, status = output.rpartition("\n")
        if (
            not separator
            or len(status) != 3
            or not status.isdigit()
            or not 200 <= int(status) < 300
        ):
            raise RuntimeError("local catalog returned an unexpected HTTP status")
        if len(body.encode()) > limit:
            raise RuntimeError("oversized local catalog response")
        return json.loads(body)

    credentials = request(
        "oauth/tokens",
        form="grant_type=client_credentials&client_id=demo&client_secret=demo",
    )
    token = credentials.get("access_token")
    if (
        not isinstance(token, str)
        or not 0 < len(token) <= 65536
        or any(not 32 < ord(character) < 127 for character in token)
    ):
        raise RuntimeError("invalid local OAuth bearer token")

    def pages(path, field):
        values, seen, page_token = [], set(), None
        for _ in range(100):
            suffix = "?pageToken=" + quote(page_token, safe="") if page_token else ""
            body = request(path + suffix, token=token)
            if not isinstance(body[field], list):
                raise RuntimeError("invalid catalog collection")
            values.extend(body[field])
            page_token = body.get("next-page-token")
            if not page_token:
                return values
            if not isinstance(page_token, str) or page_token in seen:
                raise RuntimeError("invalid catalog pagination")
            seen.add(page_token)
        raise RuntimeError("catalog listing exceeded bounded page limit")

    result = {}
    for namespace in pages("namespaces", "namespaces"):
        if (
            not isinstance(namespace, list)
            or not namespace
            or not all(
                isinstance(part, str)
                and part
                and part not in {".", ".."}
                and not any(ord(character) < 32 for character in part)
                for part in namespace
            )
        ):
            raise RuntimeError("invalid catalog namespace")
        encoded = quote("\x1f".join(namespace), safe="")
        for identifier in pages("namespaces/" + encoded + "/tables", "identifiers"):
            name = identifier["name"]
            if not name.startswith("recovery_"):
                continue
            run_id = valid_uuid(str(uuid.UUID(hex=name.removeprefix("recovery_"))))
            if (
                namespace != ["lab"]
                or identifier["namespace"] != namespace
                or name != "recovery_" + uuid.UUID(run_id).hex
                or run_id in result
            ):
                raise RuntimeError("unknown/multiple recovery table identity")
            body = request("namespaces/" + encoded + "/tables/" + name, token=token)
            if body["metadata"]["format-version"] != 2:
                raise RuntimeError("recovery table is not Iceberg v2")
            result[run_id] = body["metadata"]["table-uuid"]
            if snapshots:
                identifier = body["metadata"].get("current-snapshot-id")
                location = body.get("metadata-location")
                if (
                    type(identifier) is not int
                    or identifier <= 0
                    or not isinstance(location, str)
                    or not location
                ):
                    raise RuntimeError(
                        "native recovery snapshot identity is incomplete"
                    )
                result[run_id] = {
                    "table_uuid": result[run_id],
                    "snapshot_id": identifier,
                    "metadata_location": location,
                }
    return result


def check_histories(groups=None, *, active_key=None, require_closure=False):
    groups = cases_by_volume() if groups is None else groups
    checked = {}
    for key, cases in groups.items():
        stamp = fixture_stamp(cases)
        checked[key] = [check_case(case, stamp) for case, _ in cases]
    # Receipts preserve references to destroyed fixtures: deleting their directories is not silently ignored.
    closed = set()
    for path in results_root().glob("lifecycle/*/receipt.json"):
        receipt = load(path, results_root())
        if receipt["action"] == "docker-down" and receipt["passed"] is True:
            intent = load(path.parent / "intent.json", results_root())
            if (
                receipt["project"] != control.PROJECT
                or receipt["intent_sha256"] != digest(path.parent / "intent.json")
                or intent["fixtures"] != receipt["fixtures"]
                or any(receipt["after"].values())
                or receipt["image_tags_after"]
            ):
                raise RuntimeError("invalid closed-fixture teardown receipt")
            for group in receipt["fixtures"]:
                key = volume_key(group["volume"])
                if key not in checked or checked[key] != group["cases"]:
                    raise RuntimeError("closed fixture evidence changed or disappeared")
                closed.add(key)
    if require_closure and any(
        key != active_key and key not in closed for key in checked
    ):
        raise RuntimeError("missing teardown receipt for a destroyed/unknown fixture")
    return checked


def verify_history():
    inventory, groups = project_inventory(), cases_by_volume()
    key = current_volume(inventory)
    if key is None:
        raise RuntimeError("no current fixture; closed fixtures are checked offline")
    cases = groups.get(key, [])
    storage, query = control.service("storage"), control.service("clickhouse")
    native = catalog_recoveries(storage)
    if set(native) != {case.name for case, _ in cases}:
        raise RuntimeError("native catalog contains missing/unregistered UUID history")
    if not cases:
        return check_histories(groups, active_key=key, require_closure=True)
    stamp, errors = fixture_stamp(cases), []
    for case, metadata in cases:
        if case.name == COMPOUND_ARCHIVE_ID:
            try:
                check_case(case, stamp)
            except BaseException as error:
                errors.append({"run_id": case.name, "preservation": repr(error)})
            continue
        identifier = str(uuid.uuid4())
        directory = case / "reverifications" / identifier
        directory.mkdir(parents=True)
        invocation = {
            "run_id": case.name,
            "verification_id": identifier,
            "started_ns": time.time_ns(),
        }
        control.save(directory / "invocation.json", invocation)
        sampler, clients = ResourceSampler(directory, env=control.DOCKER_ENV), {}
        sampler.bind("storage", storage["Id"], "/data")
        sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
        primary, proof, metrics = None, None, None
        sampler.start()
        try:
            check_case(case, stamp, require_fresh=False, ignore_receipts=True)
            plan = control.profile(case, storage, query)
            control.tool(
                case, "recovery-verify", clients, sampler, plan, verification=identifier
            )
            proof = control.verification_proof(case, identifier)
        except BaseException as error:
            primary = error
            errors.append({"run_id": case.name, "error": repr(error)})
        finally:
            try:
                control.stop_actors(case, clients, sampler)
            except BaseException as error:
                errors.append({"run_id": case.name, "cleanup": repr(error)})
                primary = primary or error
            try:
                _, metrics = sampler.close()
                control.save(directory / "resources.json", metrics)
            except BaseException as error:
                errors.append({"run_id": case.name, "resources": repr(error)})
                primary = primary or error
        if primary:
            control.save(
                directory / "receipt.json",
                {
                    "passed": False,
                    "run_id": case.name,
                    "invocation": invocation,
                    "error": repr(primary),
                    "completed_ns": time.time_ns(),
                },
            )
            if isinstance(primary, (KeyboardInterrupt, SystemExit)):
                raise primary
        else:
            try:
                original, result_path, manifest = base_artifacts(case)
                native_proof = check_verification(
                    case, proof, metadata, manifest, stamp["latest_operation_ns"]
                )
                if native_proof["snapshot"]["table_uuid"] != native[case.name]:
                    raise RuntimeError("native catalog table UUID changed")
                add_artifact(manifest, directory / "invocation.json", case)
                add_artifact(manifest, directory / "resources.json", case)
                control.save(
                    directory / "receipt.json",
                    {
                        "passed": True,
                        **proof,
                        "run_id": case.name,
                        "invocation": invocation,
                        "volume": metadata["volume"],
                        "case_sha256": digest(case / "case.json"),
                        "base_result_sha256": digest(result_path),
                        "fixture": stamp,
                        "artifacts": manifest,
                        "completed_ns": time.time_ns(),
                    },
                )
            except BaseException as error:
                errors.append({"run_id": case.name, "qualification": repr(error)})
                control.save(
                    directory / "receipt.json",
                    {
                        "passed": False,
                        "run_id": case.name,
                        "invocation": invocation,
                        "error": repr(error),
                        "completed_ns": time.time_ns(),
                    },
                )
    if errors:
        raise RuntimeError(
            "history re-verification failed; preserve fixture: " + json.dumps(errors)
        )
    return check_histories(groups, active_key=key, require_closure=True)


def image_inventory():
    images = {}
    for name in IMAGES:
        if control.call(
            ["docker", "image", "ls", "-q", "--filter", "reference=" + name]
        ).strip():
            (value,) = json.loads(control.call(["docker", "image", "inspect", name]))
            if (
                value["Config"].get("Labels", {}).get("org.examples.owner")
                != control.OWNER
            ):
                raise RuntimeError(
                    "refuse image without matching ownership label: " + name
                )
            images[name] = value["Id"]
    return images


def teardown(action):
    groups, before = cases_by_volume(), project_inventory()
    key = current_volume(before)
    checked = check_histories(groups, active_key=key, require_closure=True)
    for value in before["containers"]:
        role = value["Config"]["Labels"]["com.docker.compose.service"]
        if (role.startswith("recovery-") or role == "compaction-upload-gate") and (
            value["State"]["Running"] or value["State"]["Pid"]
        ):
            raise RuntimeError("live/unfenced recovery client remains; preserve state")
    if key is not None and key in groups:
        (storage,) = [
            value
            for value in before["containers"]
            if value["Config"]["Labels"]["com.docker.compose.service"] == "storage"
        ]
        if (
            not storage["State"]["Running"]
            or storage["State"].get("Health", {}).get("Status") != "healthy"
        ):
            raise RuntimeError(
                "native catalog must be healthy for final UUID discovery"
            )
        native = catalog_recoveries(storage)
        expected = {value["run_id"]: value["table_uuid"] for value in checked[key]}
        if native != expected:
            raise RuntimeError(
                "native catalog UUIDs differ from registered verified cases"
            )
    elif key is not None:
        storage = [
            value
            for value in before["containers"]
            if value["Config"]["Labels"]["com.docker.compose.service"] == "storage"
        ]
        if (
            not storage
            or not storage[0]["State"]["Running"]
            or catalog_recoveries(storage[0])
        ):
            raise RuntimeError(
                "unregistered native history or unavailable catalog; preserve state"
            )
    images = image_inventory()
    rendered = json.loads(
        control.call(control.COMPOSE + PROFILES + ["config", "--format", "json"])
    )
    if set(rendered["services"]) != SERVICES:
        raise RuntimeError("teardown topology differs from the declared owned services")
    directory = results_root() / "lifecycle" / str(uuid.uuid4())
    directory.mkdir(parents=True)
    fixtures = [
        {"volume": dict(zip(VOLUME_FIELDS, identity)), "cases": cases}
        for identity, cases in checked.items()
    ]
    control.save(
        directory / "intent.json",
        {
            "action": action,
            "created_ns": time.time_ns(),
            "fixtures": fixtures,
            "before": before,
            "images": images,
        },
    )
    if action == "down":
        control.call(
            control.COMPOSE
            + PROFILES
            + ["rm", "--stop", "--force", *sorted(SERVICES - {"storage"})],
            timeout=180,
        )
        # Compose versions differ in whether rm includes one-offs. Remove only remaining positively fenced owned IDs.
        for value in project_inventory()["containers"]:
            if value["Config"]["Labels"]["com.docker.compose.service"] != "storage":
                if value["State"]["Running"] or value["State"]["Pid"]:
                    raise RuntimeError(
                        "application container remains running after down"
                    )
                control.call(["docker", "rm", value["Id"]])
    else:
        control.call(
            control.COMPOSE + PROFILES + ["down", "--volumes", "--remove-orphans"],
            timeout=180,
        )
    after = project_inventory()
    if action == "docker-down":
        if any(after.values()):
            raise RuntimeError(
                "owned containers/volumes/networks remain after teardown"
            )
        for name in images:
            (current,) = json.loads(control.call(["docker", "image", "inspect", name]))
            if (
                current["Id"] != images[name]
                or current["Config"].get("Labels", {}).get("org.examples.owner")
                != control.OWNER
            ):
                raise RuntimeError("owned image identity changed before removal")
            control.call(["docker", "image", "rm", name])
        if image_inventory():
            raise RuntimeError("owned image tags remain after teardown")
    else:
        if any(
            value["Config"]["Labels"]["com.docker.compose.service"] != "storage"
            for value in after["containers"]
        ):
            raise RuntimeError("application/one-shot resources remain after down")
        if {volume_key(value) for value in after["volumes"]} != {
            volume_key(value) for value in before["volumes"]
        }:
            raise RuntimeError("down unexpectedly changed retained volumes")
    control.save(
        directory / "receipt.json",
        {
            "passed": True,
            "action": action,
            "project": control.PROJECT,
            "completed_ns": time.time_ns(),
            "fixtures": fixtures,
            "after": after,
            "removed_image_tags": list(images) if action == "docker-down" else [],
            "image_tags_after": {} if action == "docker-down" else images,
            "intent_sha256": digest(directory / "intent.json"),
        },
    )


def main():
    if sys.version_info < (3, 11):
        raise RuntimeError("recovery cleanup requires Python 3.11 or newer")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "down",
            "docker-down",
            "verify-history",
            "retire-failed-case",
            "archive-prefault-case",
            "archive-compound-case",
        ],
    )
    parser.add_argument("--case")
    parser.add_argument("--source-manifest")
    parser.add_argument("--original-manifest")
    parser.add_argument("--completed-verification")
    args = parser.parse_args()
    action = args.action
    retirement_args = (args.case, args.source_manifest, args.original_manifest)
    if action == "archive-compound-case":
        if not args.case or any(
            (args.source_manifest, args.original_manifest, args.completed_verification)
        ):
            parser.error("compound preservation requires only the registered case")
    elif action == "archive-prefault-case":
        if (
            not args.case
            or not args.completed_verification
            or args.source_manifest
            or args.original_manifest
        ):
            parser.error(
                "prefault archive requires case and completed-verification only"
            )
    elif action == "retire-failed-case":
        if not all(retirement_args):
            parser.error(
                "retirement requires case, source-manifest and original-manifest"
            )
    elif any(retirement_args) or args.completed_verification:
        parser.error("retirement arguments require retire-failed-case")
    signal.signal(signal.SIGTERM, control.interrupt_controller)
    results = results_root()
    results.mkdir(exist_ok=True)
    descriptor = os.open(
        results / "controller.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644
    )
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        control.local_docker()
        if action in {
            "retire-failed-case",
            "archive-prefault-case",
            "archive-compound-case",
        }:
            case = Path(args.case).resolve()
            registered = {
                (control.ROOT / record["case"]).resolve() for record in index_records()
            }
            if case not in registered:
                raise RuntimeError(
                    "retirement case must be registered in this example's fixture index"
                )
            if action == "archive-compound-case":
                from compound_archive import archive_case

                print(archive_case(case))
                return
            if action == "archive-prefault-case":
                from prefault_archive import archive_case

                print(archive_case(case, Path(args.completed_verification).resolve()))
                return
            import recovery_retirement

            print(
                recovery_retirement.retire_case(
                    case,
                    Path(args.source_manifest).resolve(),
                    Path(args.original_manifest).resolve(),
                    completed_verification=Path(args.completed_verification).resolve()
                    if args.completed_verification
                    else None,
                )
            )
        elif action == "verify-history":
            verify_history()
        else:
            teardown(action)


if __name__ == "__main__":
    main()
