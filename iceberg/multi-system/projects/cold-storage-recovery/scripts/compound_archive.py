"""Preserve the identified reader-abort/storage-OOM failure without passing it.

Publication requires the caller's controller lock and local-Docker preflight.
Offline replay is independent of the retired Docker objects. This terminal
protocol authorizes guarded fixture teardown, never operational qualification.
"""

import hashlib
import json
from pathlib import Path
import time
import uuid

import cold_cleanup
import cold_readback as readback
import recovery_cleanup as cleanup
import recovery_control as control

RUN_ID = "2b463a19-1a8c-43bf-b84d-5a0fa03f5836"
CASE_SHA = "607c3d6b3048cd5268c3d3b9ba743575d11d7927262eef354c36adc46d9167c1"
SOURCE_FILE = "preservation/source-aeb9caa3f23e4d99b6fa14de5f47d112/manifest.json"
SOURCE_SHA = "f2ab00f0e237364e73ae95db19df5ce435f8d83e9622a5844ac03c80e07343d8"
ORIGINAL_FILE = "preservation/original-artifacts-aeb9caa3f23e4d99b6fa14de5f47d112.json"
ORIGINAL_SHA = "938d76ae6c542a349e7d1b2baf184b23fe448620c1c516ffd4079d6f2d7fd22d"
CHECKPOINT_ID = "c5f56820-970c-4eb3-82b1-a783ab526c06"
ARCHIVE_SHA = "db3d7b51ca0fd33f4d05b80d35f472df3a0f35d94470d27d526bc07234a0cb0c"
BASELINE_ID = "0a1b3af7-e6c1-4aba-84db-cef69b0eb4e9"
BASELINE_SHA = "2f570be462a7457974157141a5bec6a50d87e112b2ddd9a0c5b5640eeda48c32"
READBACK_ID = "0aaeb59c-fca3-4687-8337-12c38b57313d"
PROOF_SHA = "8fef08326984c226d76f4134ddcadf36bfa828a743775ab92cd1fce1c93a348a"
TABLE_UUID = "f4bf9a2a-70e7-4668-ac30-112a8acd9b94"
BODY_SHA = "404308f8e350e15a3ca87416cd1f712b66f2506aed41b9d38eb1c41ba432b1c0"
DIRECTORY = "compound-archives"
STATUS = "failed-but-preserved"
EXPECTED_TABLES = {
    RUN_ID: TABLE_UUID,
    "f1b2af24-f6c2-4bd2-8e44-a35392666f23": "1fc2ed32-ff57-4799-93c8-e8f64ae113a1",
}
REGISTRIES = {
    "processes": set(
        "08049c398d1e40e9999d4f29e87fa8f1 114875c68ea94550ae471b9aa408e9e2 478cfc26cec4493d9f0286f4e45d25fe 665742ad50614943a2c2e98cc78687bb ad8179b97a2041c8bcd32c431ddfbe84 c4f71641ea3c473e8e77554c3eb3516b e79de86d147d4205a8d0161882b45072 c7128de2a20a4f869a3720e60ddeff9f 56a989f427474facb52695a7900f4fe8 d4553497d4b94d2f9f309aae251dbf9e fc746702b07d4cf48ac39767b0a03150 beae6b2db4cd4d659dbfec770a638842 7c7eaa7ac98e49d1b3f0e841261dd84f".split()
    ),
    "maintenance": set(
        "15211c7f17b14154a736edcc5192de9a 3d03916f185448488f0a4963897cc52e 46c7a012f11c409eb6b4f8dd601a4320 e48ac9b3da614031b512124f19040890".split()
    ),
}


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _case(value):
    case = cold_cleanup.owned(value)
    if case.name != RUN_ID:
        raise RuntimeError("only the identified compound failure may be preserved")
    return case


def _files(case):
    files = {}
    for path in sorted(case.rglob("*")):
        relative = path.relative_to(case)
        if relative.parts[0] == DIRECTORY:
            continue
        cleanup.regular(path, case)
        if path.is_file():
            files[str(relative)] = cleanup.digest(path)
    return files


def _seal(case, relative, expected_sha, count, files, *, source=False):
    if files.get(relative) != expected_sha:
        raise RuntimeError("pinned original preservation seal changed")
    path = case / relative
    seal = cleanup.load(path, case)
    if seal["run_id"] != RUN_ID or len(seal["files"]) != count:
        raise RuntimeError("original preservation seal identity/set differs")
    for name, entry in seal["files"].items():
        local = Path(name)
        if local.is_absolute() or ".." in local.parts:
            raise RuntimeError("sealed evidence path escaped case")
        target = (path.parent if source else case) / local
        if (
            files.get(str(target.relative_to(case))) != entry["sha256"]
            or target.stat().st_size != entry["bytes"]
        ):
            raise RuntimeError("original sealed evidence changed")
    return seal


def _clients(case, originals):
    clients = []
    for parent, expected in REGISTRIES.items():
        if {p.name for p in (case / parent).iterdir()} != expected:
            raise RuntimeError("exact 13+4 process registry set changed")
        for name in sorted(expected):
            directory = case / parent / name
            launch, started, ended = [
                cleanup.load(directory / file, case)
                for file in ("launch.json", "started.json", "ended.json")
            ]
            relative = str(directory.relative_to(case))
            cold_cleanup.fenced(ended)
            if (
                launch["run_id"] != RUN_ID
                or launch["invocation"] != name
                or cold_cleanup.identity(started) != cold_cleanup.identity(ended)
            ):
                raise RuntimeError("registered process identity/fence differs")
            if relative not in originals and (
                parent != "processes"
                or launch["service"] != "recovery-tools"
                or launch["command"] != "recovery-verify"
            ):
                raise RuntimeError("post-failure actor is not a read-only verifier")
            clients.append(
                {
                    "directory": relative,
                    "launch": launch,
                    "record": {
                        key: ended[key]
                        for key in ("id", "image_id", "state", "restart_count")
                    },
                }
            )
    if len({c["record"]["id"] for c in clients}) != 17:
        raise RuntimeError("duplicate process/native container identity")
    return clients


def original_evidence(value):
    """Read-only: rehash both pinned seals and every later case artifact."""
    case = _case(value)
    if (
        cleanup.digest(case / "case.json") != CASE_SHA
        or (case / "result.json").exists()
        or (case / "compactor-evidence.json").exists()
    ):
        raise RuntimeError("original failed case identity/verdict changed")
    metadata, failure = [
        cleanup.load(case / name, case) for name in ("case.json", "failure.json")
    ]
    if (
        metadata["run_id"] != RUN_ID
        or metadata["kind"] != "iceberg-compactor-crash"
        or metadata["project"] != control.PROJECT
        or failure["passed"] is not False
        or failure.get("native_storage_preserved") is not True
        or "reader-000" not in failure["error"]
    ):
        raise RuntimeError("not the identified compound operational failure")
    files = _files(case)
    source = _seal(case, SOURCE_FILE, SOURCE_SHA, 47, files, source=True)
    original = _seal(case, ORIGINAL_FILE, ORIGINAL_SHA, 151, files)
    if (
        source["case_sha256"] != CASE_SHA
        or {n: e["sha256"] for n, e in source["files"].items()}
        != metadata["source_sha256"]
    ):
        raise RuntimeError("preserved source does not match original case")
    originals = {
        str(Path(n).parent) for n in original["files"] if n.endswith("/launch.json")
    }
    clients = _clients(case, originals)
    reader = next(c for c in clients if c["launch"]["role"] == "reader-000")
    oom = cleanup.load(case / "preservation/storage-oom-before-restart.json", case)
    restart = cleanup.load(
        case / "preservation/storage-restarted-retained-volume.json", case
    )
    killed = cleanup.load(case / "compactor-after-kill.json", case)
    if (
        reader["record"]["state"]["ExitCode"] != 2
        or oom["Id"] != metadata["storage"]["id"]
        or oom["Image"] != metadata["storage"]["image_id"]
        or not oom["State"]["OOMKilled"]
        or oom["State"]["ExitCode"] != 137
        or oom["State"]["Running"]
        or oom["State"]["Pid"] != 0
        or restart["Id"] != oom["Id"]
        or restart["Image"] != oom["Image"]
        or restart["State"]["StartedAt"] == oom["State"]["StartedAt"]
        or killed["state"]["ExitCode"] != 137
        or killed["state"]["OOMKilled"]
    ):
        raise RuntimeError(
            "reader abort, separate storage OOM or retained restart evidence differs"
        )
    for observed in (oom, restart):
        if [(m.get("Name"), m["Destination"], m["RW"]) for m in observed["Mounts"]] != [
            (metadata["volume"]["Name"], "/data", True)
        ]:
            raise RuntimeError("storage failure/restart used different native storage")
    latest = max(
        [
            metadata["created_ns"],
            *[
                cleanup.event_time(case / n)
                for n in files
                if Path(n).suffix in (".json", ".jsonl")
            ],
        ]
    )
    failure_files = {
        n: files[n]
        for n in (
            "case.json",
            "failure.json",
            "cleanup-errors.json",
            "compactor-after-kill.json",
            "preservation/storage-oom-before-restart.json",
            "preservation/storage-restarted-retained-volume.json",
        )
    }
    return {
        "metadata": metadata,
        "artifacts": files,
        "original_directories": sorted(originals),
        "clients": clients,
        "latest_operation_ns": latest,
        "failure_fingerprint": _hash(failure_files),
    }


def fingerprint(case):
    """Actual failed evidence, including partial maintenance; no invented final proof."""
    case = _case(case)
    evidence = original_evidence(case)
    return {
        "files": {
            str((case / n).relative_to(control.ROOT)): sha
            for n, sha in evidence["artifacts"].items()
        },
        "latest_operation_ns": evidence["latest_operation_ns"],
    }


def _preservation(case, metadata):
    parent = cleanup.results_root() / "cold-storage" / CHECKPOINT_ID
    parent, checkpoint, restored = readback.checkpoint(parent)
    if (
        cleanup.volume_key(checkpoint["source_volume"])
        != cleanup.volume_key(metadata["volume"])
        or cleanup.digest(parent / "storage.tar.gz") != ARCHIVE_SHA
    ):
        raise RuntimeError("cold archive is not the pinned original native fixture")
    _, _, baseline_path, baseline, witnesses = readback.fixture(
        case, BASELINE_ID, checkpoint["source_volume"]
    )
    if cleanup.digest(baseline_path) != BASELINE_SHA:
        raise RuntimeError("source diagnostic body proof changed")
    root = parent / "native-readback" / READBACK_ID
    selected = cold_cleanup.plan(root, "readback")
    invocation = cleanup.load(root / "invocation.json", root)
    proof = cleanup.load(root / "result.json", root)["verification"]
    directory = (
        root / ("recovery-" + RUN_ID) / "verifications" / proof["verification_id"]
    )
    proof_path = directory / "verified.json"
    built = cleanup.load(root / "built-source.json", root)
    if (
        cleanup.digest(proof_path) != PROOF_SHA
        or invocation["case"] != str(case.relative_to(control.ROOT))
        or invocation["baseline_verification"] != BASELINE_ID
        or invocation["baseline_sha256"] != BASELINE_SHA
        or invocation["source_witnesses"] != witnesses
    ):
        raise RuntimeError("native readback is not the pinned source-history replay")
    readback.validate_proof(
        proof, baseline, RUN_ID, proof["verification_id"], built["binary_sha256"]
    )
    body = proof["exports"]["duckdb"]["proof"]
    if (
        proof["passed"] is not False
        or proof["qualified"] is not False
        or proof["diagnostic"] is not True
        or proof["actor_lifecycle_passed"] is not False
        or proof["strict_mutation_history_passed"] is not True
        or proof["reader_contract_passed"] is not True
        or proof["snapshot"]["table_uuid"] != TABLE_UUID
        or proof["snapshot"]["snapshot_id"] != 2119811354969392301
        or body["acknowledged_rows"] != 3390000
        or body["rows_verified"] != 3390000
        or body["canonical_body_sha256"] != BODY_SHA
    ):
        raise RuntimeError(
            "preservation must retain the original failed qualification and exact bodies"
        )
    external = {
        str((root / n).relative_to(control.ROOT)): sha
        for n, sha in selected["artifacts"].items()
    }
    for name, expected in witnesses.items():
        file = root / ("recovery-" + RUN_ID) / name
        if cleanup.digest(cleanup.regular(file, root)) != expected:
            raise RuntimeError("restored witness copy changed")
        external[str(file.relative_to(control.ROOT))] = expected
    for name in (
        "invocation.json",
        "result.json",
        "archive-proof.json",
        "archive.sha256",
        "storage.tar.gz",
        "files.sha256",
        "metadata.txt",
        "restored-files.sha256",
        "restored-metadata.txt",
        "restore-volume.json",
        "storage-stopped.json",
        "clickhouse-stopped.json",
        "backup-final.json",
        "restore-final.json",
        "backup-console.log",
        "restore-console.log",
        "backup-resources.json",
        "restore-resources.json",
    ):
        external[str((parent / name).relative_to(control.ROOT))] = cleanup.digest(
            cleanup.regular(parent / name, parent)
        )
    metrics_path = directory / "storage-metrics.json"
    metrics = cleanup.load(metrics_path, root)
    if proof["storage_metrics"]["sha256"] != cleanup.digest(metrics_path):
        raise RuntimeError("native readback storage measurement changed")
    native_artifacts = {}
    cleanup.check_native_versions(
        directory, proof, metrics, {"duckdb_version": "2.0.1"}, native_artifacts, root
    )
    versions = cleanup.load(directory / "native-versions.json", root)
    if (
        versions["engine"] != "v2.0.1"
        or versions["lab_binary_sha256"] != built["binary_sha256"]
    ):
        raise RuntimeError("native restored-reader binary/version differs")
    external.update(
        {
            str((root / n).relative_to(control.ROOT)): sha
            for n, sha in native_artifacts.items()
        }
    )
    external[str(metrics_path.relative_to(control.ROOT))] = cleanup.digest(metrics_path)
    return {
        "checkpoint": str(parent.relative_to(control.ROOT)),
        "checkpoint_id": CHECKPOINT_ID,
        "archive_sha256": ARCHIVE_SHA,
        "restore_volume": restored,
        "verification_id": proof["verification_id"],
        "verification_sha256": PROOF_SHA,
        "table_uuid": TABLE_UUID,
        "snapshot": proof["snapshot"],
        "acknowledged_rows": body["acknowledged_rows"],
        "body_sha256": BODY_SHA,
        "external_artifacts": external,
    }


def _native_fences(clients):
    identifiers = set(
        control.call(["docker", "ps", "--all", "--no-trunc", "-q"]).split()
    )
    running = control.call(
        [
            "docker",
            "ps",
            "--no-trunc",
            "-q",
            "--filter",
            "label=org.examples.recovery.run=" + RUN_ID,
        ]
    ).split()
    if running:
        raise RuntimeError("live actor still references the failed run")
    fences = []
    for client in clients:
        record, launch = client["record"], client["launch"]
        native = None
        if record["id"] in identifiers:
            raw = control.owned(record["id"], launch["service"])
            labels = raw["Config"]["Labels"]
            native = control.state(raw)
            if (
                native != record
                or raw["Name"] != "/" + launch["name"]
                or labels.get("org.examples.recovery.run") != RUN_ID
                or labels.get("org.examples.recovery.invocation")
                != launch["invocation"]
            ):
                raise RuntimeError("fresh native process fencing/identity differs")
        fences.append(
            {"directory": client["directory"], "record": record, "native": native}
        )
    return {
        "all_container_ids": sorted(identifiers),
        "running_run_ids": running,
        "fences": fences,
        "observed_ns": time.time_ns(),
    }


def _check_fences(clients, saved):
    if saved["running_run_ids"] or len(saved["all_container_ids"]) != len(
        set(saved["all_container_ids"])
    ):
        raise RuntimeError("invalid positive native runtime listing")
    expected = {c["directory"]: c["record"] for c in clients}
    if len(saved["fences"]) != 17 or {f["directory"] for f in saved["fences"]} != set(
        expected
    ):
        raise RuntimeError("saved native fence set differs")
    for fence in saved["fences"]:
        record = expected[fence["directory"]]
        if fence["record"] != record or (
            fence["native"] != record
            if record["id"] in saved["all_container_ids"]
            else fence["native"] is not None
        ):
            raise RuntimeError(
                "saved fence does not prove the recorded process stopped/absent"
            )


def preserved_case(value, stamp, require_fresh=True):
    """Offline check_case hook; a preservation pass never changes operational failure."""
    case = _case(value)
    attempts = []
    for directory in (case / DIRECTORY).glob("*"):
        cleanup.valid_uuid(directory.name)
        invocation = cleanup.load(directory / "invocation.json", case)
        if invocation["run_id"] != RUN_ID or invocation["archive_id"] != directory.name:
            raise RuntimeError("compound archive invocation identity differs")
        attempts.append((invocation["started_ns"], directory, invocation))
    if not attempts:
        raise RuntimeError("compound failure requires a completed preservation archive")
    _, directory, invocation = max(attempts)
    receipt = cleanup.load(directory / "receipt.json", case)
    evidence = original_evidence(case)
    preservation = _preservation(case, evidence["metadata"])
    if (
        receipt.get("passed") is not False
        or receipt.get("status") != STATUS
        or receipt.get("data_preservation_passed") is not True
        or receipt.get("original_status") != "failed"
        or receipt["run_id"] != RUN_ID
        or receipt["archive_id"] != directory.name
        or receipt["case_sha256"] != CASE_SHA
        or receipt["source_seal_sha256"] != SOURCE_SHA
        or receipt["original_seal_sha256"] != ORIGINAL_SHA
        or receipt["invocation"] != invocation
        or receipt["artifacts"] != evidence["artifacts"]
        or receipt["artifacts_fingerprint"] != _hash(evidence["artifacts"])
        or receipt["failure_fingerprint"] != evidence["failure_fingerprint"]
        or receipt["preservation"] != preservation
        or receipt["preservation_fingerprint"] != _hash(preservation)
        or receipt["native_tables"] != EXPECTED_TABLES
        or {run: head["table_uuid"] for run, head in receipt["native_heads"].items()}
        != EXPECTED_TABLES
        or receipt["native_heads"].get(RUN_ID) != preservation["snapshot"]
        or cleanup.volume_key(receipt["volume"])
        != cleanup.volume_key(evidence["metadata"]["volume"])
        or (require_fresh and receipt["fixture"] != stamp)
    ):
        raise RuntimeError("compound preservation is stale, changed or incomplete")
    _check_fences(evidence["clients"], receipt["native_fences"])
    if (
        receipt["native_fences"]["observed_ns"] < invocation["started_ns"]
        or receipt["completed_ns"] < receipt["native_fences"]["observed_ns"]
    ):
        raise RuntimeError("native fences do not belong to this publication")
    artifacts = evidence["artifacts"] | {
        "@external:" + n: sha for n, sha in preservation["external_artifacts"].items()
    }
    for file in (directory / "invocation.json", directory / "receipt.json"):
        artifacts[str(file.relative_to(case))] = cleanup.digest(file)
    return {
        "case": str(case.relative_to(control.ROOT)),
        "run_id": RUN_ID,
        "verification_id": preservation["verification_id"],
        "verification_sha256": PROOF_SHA,
        "table_uuid": TABLE_UUID,
        "artifacts": artifacts,
        "original_status": "failed",
        "data_preservation_passed": True,
    }


def archive_case(value):
    """Caller holds controller.lock/local_docker; this function never mutates the SUT."""
    case = _case(value)
    evidence = original_evidence(case)
    preservation = _preservation(case, evidence["metadata"])
    directory = cleanup.regular(case / DIRECTORY / str(uuid.uuid4()), case)
    directory.mkdir(parents=True)
    invocation = {
        "run_id": RUN_ID,
        "archive_id": directory.name,
        "started_ns": time.time_ns(),
    }
    control.save(directory / "invocation.json", invocation)
    try:
        inventory, groups = cleanup.project_inventory(), cleanup.cases_by_volume()
        key = cleanup.volume_key(evidence["metadata"]["volume"])
        if cleanup.current_volume(inventory) != key:
            raise RuntimeError(
                "current original native fixture differs from checkpoint"
            )
        native_heads = cleanup.catalog_recoveries(
            control.service("storage"), snapshots=True
        )
        native = {run: head["table_uuid"] for run, head in native_heads.items()}
        if (
            set(native) != {c.name for c, _ in groups[key]}
            or native != EXPECTED_TABLES
            or native_heads[RUN_ID] != preservation["snapshot"]
        ):
            raise RuntimeError(
                "current native UUID inventory differs from preserved fixture"
            )
        stamp = cleanup.fixture_stamp(groups[key])
        native_fences = _native_fences(evidence["clients"])
        if (
            original_evidence(case) != evidence
            or _preservation(case, evidence["metadata"]) != preservation
        ):
            raise RuntimeError("preserved evidence changed during native fencing")
        if (
            cleanup.catalog_recoveries(control.service("storage"), snapshots=True)
            != native_heads
        ):
            raise RuntimeError(
                "native catalog head changed during preservation publication"
            )
        receipt = {
            "passed": False,
            "status": STATUS,
            "original_status": "failed",
            "data_preservation_passed": True,
            "run_id": RUN_ID,
            "archive_id": directory.name,
            "case_sha256": CASE_SHA,
            "source_seal_sha256": SOURCE_SHA,
            "original_seal_sha256": ORIGINAL_SHA,
            "invocation": invocation,
            "volume": evidence["metadata"]["volume"],
            "artifacts": evidence["artifacts"],
            "artifacts_fingerprint": _hash(evidence["artifacts"]),
            "failure_fingerprint": evidence["failure_fingerprint"],
            "preservation": preservation,
            "preservation_fingerprint": _hash(preservation),
            "fixture": stamp,
            "native_fences": native_fences,
            "native_tables": native,
            "native_heads": native_heads,
            "completed_ns": time.time_ns(),
            "scope": "Terminal preservation before guarded teardown only. Original reader abort, storage OOM and uncompleted compactor recovery remain FAILED. Archive and restored volumes are retained; no production, availability or performance qualification.",
        }
    except BaseException as primary:
        try:
            control.save(
                directory / "receipt.json",
                {
                    "passed": False,
                    "status": "failed-preservation",
                    "data_preservation_passed": False,
                    "invocation": invocation,
                    "error": repr(primary),
                    "completed_ns": time.time_ns(),
                },
            )
        except BaseException as secondary:
            primary.add_note(
                "Could not preserve failed publication: " + repr(secondary)
            )
        raise
    control.save(directory / "receipt.json", receipt)
    preserved_case(case, stamp)
    return directory / "receipt.json"
