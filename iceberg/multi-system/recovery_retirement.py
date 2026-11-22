"""Preserve one identified catalog-recorder precision failure, without passing it.

This is deliberately not an escape hatch for arbitrary failed recovery cases. The
allowlisted original seals bind the failed run, its source and every old artifact.
Only a fresh, normal, read-only verifier can qualify preservation of its data.
"""

from __future__ import annotations

from pathlib import Path
import time
import uuid

import recovery_cleanup as cleanup
import recovery_control as control
from recovery_resources import ResourceSampler, require_coverage, summarize

RUN_ID = "36e77aab-4a09-4bf8-8192-2b4ed20a7696"
SOURCE_SHA = "aee902261dfcc4e9f329d83396090480a54e934377c13e023b7a2a063103eeae"
ARTIFACT_SHA = "4c75645eac8e9674c48243e6f26563c4d048561741fc6f9479468b87540bc141"
CATALOG_SHA = "fd11a0db0743d5cd46445b6c6f45b966350b9193e73e373538f3679f1db76def"
CATALOG_FILE = "preservation/native-loadtable-74df384afe8e4196ab14e72b47f3bc09.json"
ORIGINAL_VERIFY = "8d1800ad-fd4c-41af-9d6b-781656f0db9d"
ROUNDED_SNAPSHOT = 2696719651453291500
ACTUAL_SNAPSHOT = 2696719651453291395
STATUS = "failed-but-archived"
CATALOG_RECORDS = (
    "verify-start-catalog.json",
    "verify-duckdb-catalog.json",
    "verify-clickhouse-catalog.json",
)


def _sealed_files(path, root, expected_sha, manifest, *, source=False):
    if cleanup.digest(cleanup.regular(path, root)) != expected_sha:
        raise RuntimeError("original preservation seal changed")
    seal = cleanup.load(path, root)
    if seal["run_id"] != RUN_ID or not seal["files"]:
        raise RuntimeError("original preservation seal identity differs")
    for name, entry in seal["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("invalid preservation seal path")
        target = (
            cleanup.regular(path.parent / relative, root)
            if source
            else cleanup.regular(root / relative, root)
        )
        if (
            cleanup.digest(target) != entry["sha256"]
            or target.stat().st_size != entry["bytes"]
        ):
            raise RuntimeError("original sealed artifact changed: " + name)
        cleanup.add_artifact(manifest, target, root)
    cleanup.add_artifact(manifest, path, root)
    return seal


def original_evidence(
    case, source_manifest, original_manifest, *, artifact_cutoff_ns=None
):
    """Offline eligibility; rehash old CSVs, witnesses, sources and failed verdicts."""
    case = cleanup.regular(Path(case).resolve(), cleanup.results_root())
    if case.name != RUN_ID or (case / "result.json").exists():
        raise RuntimeError(
            "only the identified failed instrumentation case is eligible"
        )
    metadata = cleanup.load(case / "case.json", case)
    if (
        metadata["run_id"] != case.name
        or metadata["project"] != control.PROJECT
        or metadata["kind"] != "iceberg-storage-crash"
    ):
        raise RuntimeError("original failed case identity differs")
    manifest = {}
    source = _sealed_files(
        Path(source_manifest), case, SOURCE_SHA, manifest, source=True
    )
    original = _sealed_files(Path(original_manifest), case, ARTIFACT_SHA, manifest)
    if source["original_case_sha256"] != cleanup.digest(case / "case.json") or set(
        source["files"]
    ) != set(metadata["source_sha256"]):
        raise RuntimeError("original frozen source set differs")
    for name, checksum in metadata["source_sha256"].items():
        if source["files"][name]["sha256"] != checksum:
            raise RuntimeError("original source does not match case")
    failure = cleanup.load(case / "failure.json", case)
    needle = "Could not find snapshot with id " + str(ROUNDED_SNAPSHOT)
    if (
        failure["passed"] is not False
        or failure.get("native_storage_preserved") is not True
        or needle not in failure["error"]
        or "recovery-verify" not in failure["error"]
    ):
        raise RuntimeError("failure is not the identified instrumentation failure")
    cutoff = original["captured_ns"]
    if type(cutoff) is not int or cutoff <= metadata["created_ns"]:
        raise RuntimeError("invalid original process cutoff")
    base = {"completed_ns": cutoff}
    cleanup.check_clients(
        case,
        base,
        metadata,
        manifest,
        allow_failed_verifiers=True,
        artifact_cutoff_ns=artifact_cutoff_ns,
    )
    catalog_path = cleanup.regular(case / CATALOG_FILE, case)
    if cleanup.digest(catalog_path) != CATALOG_SHA:
        raise RuntimeError("original native catalog body changed")
    catalog = cleanup.load(catalog_path, case)
    snapshot = {
        "table_uuid": catalog["metadata"]["table-uuid"],
        "snapshot_id": catalog["metadata"]["current-snapshot-id"],
        "metadata_location": catalog["metadata-location"],
    }
    if (
        catalog["metadata"]["format-version"] != 2
        or snapshot["snapshot_id"] != ACTUAL_SNAPSHOT
    ):
        raise RuntimeError("original exact native snapshot differs")
    cleanup.add_artifact(manifest, catalog_path, case)
    workload = case / ("recovery-" + case.name)
    checksum = cleanup.digest(workload / "config.json")
    directory = workload / "verifications" / ORIGINAL_VERIFY
    invocation = cleanup.load(directory / "invocation.json", case)
    if (
        invocation["diagnostic"] is not False
        or invocation["run_id"] != case.name
        or invocation["verification_id"] != ORIGINAL_VERIFY
        or invocation["config_sha256"] != checksum
        or (directory / "verified.json").exists()
    ):
        raise RuntimeError("original verification identity/verdict differs")
    failed_storage = cleanup.load(directory / "storage-metrics.json", case)
    failed_export = cleanup.load(directory / "failure.json", case)
    if (
        failed_storage["passed"] is not False
        or needle not in failed_storage["unavailable_error"]
        or needle not in failed_export["error"]
        or failed_storage["snapshot"] != {**snapshot, "snapshot_id": ROUNDED_SNAPSHOT}
    ):
        raise RuntimeError(
            "original failure is not isolated to snapshot instrumentation"
        )
    exports = {}
    for engine in ("duckdb", "clickhouse"):
        path = directory / (engine + "-proof.json")
        exported = cleanup.load(path, case)
        if (
            exported["run_id"] != case.name
            or exported["verification_id"] != ORIGINAL_VERIFY
            or exported["config_sha256"] != checksum
            or exported["csv_file"] != engine + "-rows.csv"
            or cleanup.digest(directory / exported["csv_file"])
            != exported["csv_sha256"]
        ):
            raise RuntimeError("original complete body export changed")
        exports[engine] = exported["proof"]
    if (
        exports["duckdb"] != exports["clickhouse"]
        or exports["duckdb"]["rows_verified"] <= 0
        or exports["duckdb"]["acknowledged_rows"] <= 0
    ):
        raise RuntimeError("original complete body proofs differ")
    for name in CATALOG_RECORDS:
        old_catalog = cleanup.load(directory / name, case)
        if (
            old_catalog["available"] is not True
            or old_catalog["body_sha256"] != CATALOG_SHA
            or old_catalog["load_table"]["metadata"]["current-snapshot-id"]
            != ROUNDED_SNAPSHOT
        ):
            raise RuntimeError("original rounded catalog identity differs")
    return metadata, {"completed_ns": cutoff}, snapshot, exports["duckdb"], manifest


def native_fences(case, cutoff):
    """Failed inspect is unknown, never a successful fence. Exact old IDs must exist."""
    fences = []
    for directory in sorted((case / "processes").iterdir()):
        launch = cleanup.load(directory / "launch.json", case)
        if launch["created_host_ns"] > cutoff:
            continue
        ended = cleanup.load(directory / "ended.json", case)
        value = control.owned(ended["id"], launch["service"])
        labels = value["Config"]["Labels"]
        if (
            control.state(value)
            != {key: ended[key] for key in ("id", "image_id", "state", "restart_count")}
            or labels.get("org.examples.recovery.run") != case.name
            or labels.get("org.examples.recovery.invocation") != directory.name
            or value["State"]["Running"]
            or value["State"]["Pid"] != 0
        ):
            raise RuntimeError("original client native fencing/identity differs")
        fences.append(
            {
                "invocation": directory.name,
                "service": launch["service"],
                **control.state(value),
            }
        )
    if len(fences) != 7 or len({f["id"] for f in fences}) != 7:
        raise RuntimeError(
            "all seven original clients require independent native fences"
        )
    return fences


def _check_fences(case, cutoff, fences):
    expected = {}
    for directory in (case / "processes").iterdir():
        launch = cleanup.load(directory / "launch.json", case)
        if launch["created_host_ns"] <= cutoff:
            ended = cleanup.load(directory / "ended.json", case)
            expected[directory.name] = {
                "invocation": directory.name,
                "service": launch["service"],
                **{
                    key: ended[key]
                    for key in ("id", "image_id", "state", "restart_count")
                },
            }
    if (
        len(expected) != 7
        or len(fences) != 7
        or {value["invocation"] for value in fences} != set(expected)
        or any(value != expected[value["invocation"]] for value in fences)
    ):
        raise RuntimeError("saved original native fence set differs")


def _readonly_fence_set(case, original_cutoff, completed_ns=None):
    expected = {}
    for directory in sorted((case / "processes").iterdir()):
        launch = cleanup.load(directory / "launch.json", case)
        if launch["created_host_ns"] <= original_cutoff or (
            completed_ns is not None and launch["created_host_ns"] > completed_ns
        ):
            continue
        if (
            launch["service"] != "recovery-tools"
            or launch["command"] != "recovery-verify"
            or launch["run_id"] != case.name
            or launch["invocation"] != directory.name
        ):
            raise RuntimeError(
                "later retirement helper is not the owned read-only verifier"
            )
        ended = cleanup.load(directory / "ended.json", case)
        if ended["state"]["Running"] or ended["state"]["Pid"] != 0:
            raise RuntimeError("later retirement helper is not positively fenced")
        expected[directory.name] = {
            "invocation": directory.name,
            "service": launch["service"],
            **{key: ended[key] for key in ("id", "image_id", "state", "restart_count")},
        }
    return expected


def readonly_native_fences(case, original_cutoff):
    """Inspect every finalized later helper, including failed collector attempts."""
    expected = _readonly_fence_set(case, original_cutoff)
    if not expected or len({value["id"] for value in expected.values()}) != len(
        expected
    ):
        raise RuntimeError("missing/duplicate later retirement helper identities")
    for invocation, recorded in expected.items():
        value = control.owned(recorded["id"], "recovery-tools")
        labels = value["Config"]["Labels"]
        if (
            control.state(value)
            != {
                key: recorded[key]
                for key in ("id", "image_id", "state", "restart_count")
            }
            or labels.get("org.examples.recovery.run") != case.name
            or labels.get("org.examples.recovery.invocation") != invocation
            or value["State"]["Running"]
            or value["State"]["Pid"] != 0
        ):
            raise RuntimeError("later helper native fencing/identity differs")
    return list(expected.values())


def _check_readonly_fences(case, original_cutoff, completed_ns, fences):
    expected = _readonly_fence_set(case, original_cutoff, completed_ns)
    if (
        not expected
        or len(fences) != len(expected)
        or {value["invocation"] for value in fences} != set(expected)
        or any(value != expected[value["invocation"]] for value in fences)
    ):
        raise RuntimeError("saved later helper native fence set differs")


def _check_corrected(case, reference, metadata, snapshot, bodies, manifest, newest_ns):
    proof = cleanup.check_verification(case, reference, metadata, manifest, newest_ns)
    if proof["snapshot"] != snapshot or proof["exports"]["duckdb"]["proof"] != bodies:
        raise RuntimeError(
            "corrected verification changed original snapshot/body proof"
        )
    directory = (
        case
        / ("recovery-" + case.name)
        / "verifications"
        / reference["verification_id"]
    )
    for name in CATALOG_RECORDS:
        path = directory / name
        record = cleanup.load(path, case)
        body = record["load_table"]
        if (
            record["available"] is not True
            or record["run_id"] != case.name
            or record["config_sha256"]
            != cleanup.digest(case / ("recovery-" + case.name) / "config.json")
            or record["body_sha256"] != CATALOG_SHA
            or {
                "table_uuid": body["metadata"]["table-uuid"],
                "snapshot_id": body["metadata"]["current-snapshot-id"],
                "metadata_location": body["metadata-location"],
            }
            != snapshot
        ):
            raise RuntimeError(
                "corrected native catalog changed from original raw body"
            )
        cleanup.add_artifact(manifest, path, case)
    return proof


def corrected_client(case, reference, plan, sources, *, native=None):
    matches = []
    for directory in (case / "processes").iterdir():
        launch = cleanup.load(directory / "launch.json", case)
        if (
            launch.get("extra", {}).get("RECOVERY_VERIFICATION_ID")
            == reference["verification_id"]
        ):
            matches.append((directory, launch))
    if len(matches) != 1:
        raise RuntimeError("one exact corrected verification client required")
    directory, launch = matches[0]
    started, ended = [
        cleanup.load(directory / name, case) for name in ("started.json", "ended.json")
    ]
    expected_sources = {
        Path(name).name: checksum
        for name, checksum in sources.items()
        if name.startswith("app/")
    }
    if (
        launch["run_id"] != case.name
        or launch["invocation"] != directory.name
        or launch["service"] != "recovery-tools"
        or launch["command"] != "recovery-verify"
        or launch.get("extra", {}).get("RECOVERY_DIAGNOSTIC", "false") != "false"
        or started["image_id"] != plan["images"]["recovery-tools"]
        or started["built_source"]["sources"] != expected_sources
        or ended["clean"] is not True
        or ended["forced"]
        or ended["aborted"]
        or ended["state"]["Running"]
        or ended["state"]["Pid"] != 0
        or ended["state"]["ExitCode"] != 0
        or ended["state"]["OOMKilled"]
        or ended["state"]["StartedAt"] != started["state"]["StartedAt"]
        or ended["restart_count"] != started["restart_count"]
        or ended["id"] != started["id"]
        or ended["image_id"] != started["image_id"]
    ):
        raise RuntimeError(
            "corrected verification client identity/source/clean exit differs"
        )
    expected = {key: ended[key] for key in ("id", "image_id", "state", "restart_count")}
    if native is None:
        value = control.owned(ended["id"], "recovery-tools")
        labels = value["Config"]["Labels"]
        if (
            labels.get("org.examples.recovery.run") != case.name
            or labels.get("org.examples.recovery.invocation") != directory.name
        ):
            raise RuntimeError("corrected client native ownership differs")
        native = control.state(value)
    if native != expected:
        raise RuntimeError("corrected verification client lacks matching native fence")
    return native, launch["role"]


def readonly_artifacts(case, manifest, completed_ns):
    # Preserve intermediate failed collectors too. Later history checks have new
    # invocation timestamps and belong to their own immutable receipts.
    workload = case / ("recovery-" + case.name)
    directories = []
    for directory in (workload / "verifications").iterdir():
        invocation = cleanup.load(directory / "invocation.json", case)
        if invocation["run_id"] != case.name or invocation[
            "verification_id"
        ] != cleanup.valid_uuid(directory.name):
            raise RuntimeError("read-only verification directory identity differs")
        if invocation["utc_ns"] <= completed_ns:
            directories.append(directory)
    for directory in (case / "preservation").glob("corrected-verification-*"):
        invocation = cleanup.load(directory / "invocation.json", case)
        if (
            invocation["run_id"] != case.name
            or directory.name
            != "corrected-verification-"
            + cleanup.valid_uuid(invocation["verification_id"])
        ):
            raise RuntimeError("preserved collector invocation identity differs")
        if invocation["started_ns"] <= completed_ns:
            cleanup.load(directory / "result.json", case)
            directories.append(directory)
    for directory in directories:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                cleanup.add_artifact(manifest, path, case)


def archived_base(case):
    """Offline cleanup hook. Latest incomplete/failed retirement blocks the fixture."""
    attempts = []
    for directory in (case / "retirements").glob("*"):
        cleanup.valid_uuid(directory.name)
        invocation = cleanup.load(directory / "invocation.json", case)
        if (
            invocation["run_id"] != case.name
            or invocation["retirement_id"] != directory.name
        ):
            raise RuntimeError("retirement invocation identity differs")
        attempts.append((invocation["started_ns"], directory, invocation))
    if not attempts:
        raise RuntimeError(
            "failed recovery case requires completed archival retirement"
        )
    _, directory, invocation = max(attempts)
    receipt = cleanup.load(directory / "receipt.json", case)
    if (
        receipt.get("passed") is not False
        or receipt.get("data_preservation_passed") is not True
        or receipt.get("status") != STATUS
        or receipt["run_id"] != case.name
        or receipt["retirement_id"] != directory.name
        or receipt["invocation"] != invocation
    ):
        raise RuntimeError("retirement is incomplete/failed; retain data")
    metadata, base, snapshot, bodies, manifest = original_evidence(
        case,
        case / invocation["source_manifest"],
        case / invocation["original_manifest"],
        artifact_cutoff_ns=receipt["completed_ns"],
    )
    if (
        cleanup.volume_key(receipt["volume"]) != cleanup.volume_key(metadata["volume"])
        or receipt["case_sha256"] != cleanup.digest(case / "case.json")
        or receipt["original_process_cutoff_ns"] != base["completed_ns"]
    ):
        raise RuntimeError("retirement case/volume/cutoff differs")
    if (
        receipt["source_manifest_sha256"] != SOURCE_SHA
        or receipt["original_manifest_sha256"] != ARTIFACT_SHA
        or receipt["native_catalog_body_sha256"] != CATALOG_SHA
    ):
        raise RuntimeError("retirement seal references differ")
    _check_fences(case, base["completed_ns"], receipt["native_fences"])
    _check_readonly_fences(
        case,
        base["completed_ns"],
        receipt["completed_ns"],
        receipt["readonly_native_fences"],
    )
    _check_corrected(
        case,
        receipt,
        metadata,
        snapshot,
        bodies,
        manifest,
        invocation["verification_started_ns"],
    )
    corrected_client(
        case,
        receipt,
        cleanup.load(directory / "current-profile.json", case),
        cleanup.load(directory / "current-sources.json", case),
        native=cleanup.load(directory / "current-client.json", case),
    )
    for name in (
        "invocation.json",
        "resources.json",
        "current-profile.json",
        "current-sources.json",
        "current-client.json",
    ):
        cleanup.add_artifact(manifest, directory / name, case)
    _completed_artifacts(case, invocation, manifest)
    readonly_artifacts(case, manifest, receipt["completed_ns"])
    if receipt["artifacts"] != manifest:
        raise RuntimeError("retirement artifact manifest changed")
    cleanup.add_artifact(manifest, directory / "receipt.json", case)
    return (
        {**receipt, "completed_ns": base["completed_ns"]},
        directory / "receipt.json",
        manifest,
    )


def completed_export(case, directory, plan, sources, storage, query, newest_ns):
    """Adopt the parent's finalized read-only export, never a partial running job."""
    directory = cleanup.regular(Path(directory).resolve(), case)
    if directory.parent != case / "preservation" or not directory.name.startswith(
        "corrected-verification-"
    ):
        raise RuntimeError("completed export must be the owned preservation job")
    invocation = cleanup.load(directory / "invocation.json", case)
    identifier = cleanup.valid_uuid(invocation["verification_id"])
    result = cleanup.load(directory / "result.json", case)
    if (
        directory.name != "corrected-verification-" + identifier
        or invocation["run_id"] != case.name
        or result["verification_id"] != identifier
        or result["data_verification_passed"] is not True
        or result["original_case_passed"] is not False
        or result["error"] is not None
        or result["completed_ns"] <= invocation["started_ns"]
    ):
        raise RuntimeError("completed read-only export did not finalize cleanly")
    reference = control.verification_proof(case, identifier)
    if reference != result["reference"]:
        raise RuntimeError("completed export reference changed")
    native, role = corrected_client(case, reference, plan, sources)
    records = control.history(directory / "resources.jsonl")
    require_coverage(records, ["storage", "clickhouse", role], invocation["started_ns"])
    identities = {
        "storage": storage["Id"],
        "clickhouse": query["Id"],
        role: native["id"],
    }
    if any(
        record["role"] not in identities
        or record["container_id"] != identities[record["role"]]
        or record["host_ended_ns"] > result["completed_ns"]
        for record in records
    ):
        raise RuntimeError("completed export sampler identity/time differs")
    metrics = cleanup.load(directory / "resource-summary.json", case)
    if summarize(records) != metrics:
        raise RuntimeError("completed export resource summary changed")
    proof = cleanup.load(
        case
        / ("recovery-" + case.name)
        / "verifications"
        / identifier
        / "verified.json",
        case,
    )
    if proof["utc_ns"] < max(newest_ns, invocation["started_ns"]):
        raise RuntimeError("completed export predates latest fixture operation")
    return reference, native, metrics, invocation["started_ns"]


def _completed_artifacts(case, invocation, manifest):
    if invocation.get("completed_export"):
        directory = cleanup.regular(case / invocation["completed_export"], case)
        for name in (
            "invocation.json",
            "result.json",
            "profile.json",
            "resources.jsonl",
            "resource-summary.json",
        ):
            cleanup.add_artifact(manifest, directory / name, case)


def retire_case(
    case, source_manifest, original_manifest, *, completed_verification=None
):
    """Caller holds controller.lock and calls local_docker(). Only read-only tools run.

    Creates an immutable receipt; never edits original verdicts or authorizes a
    failed/ambiguous mutation replay. Root CLI owns lock/preflight wiring.
    """
    case = Path(case).resolve()
    metadata, base, snapshot, bodies, _ = original_evidence(
        case, source_manifest, original_manifest
    )
    identifier = str(uuid.uuid4())
    directory = case / "retirements" / identifier
    directory.mkdir(parents=True)
    invocation = {
        "run_id": case.name,
        "retirement_id": identifier,
        "verification_id": str(uuid.uuid4()),
        "started_ns": time.time_ns(),
        "source_manifest": str(Path(source_manifest).relative_to(case)),
        "original_manifest": str(Path(original_manifest).relative_to(case)),
    }
    if completed_verification is not None:
        invocation["completed_export"] = str(
            Path(completed_verification).resolve().relative_to(case)
        )
        invocation["verification_id"] = cleanup.load(
            Path(completed_verification) / "invocation.json", case
        )["verification_id"]
        invocation["verification_started_ns"] = cleanup.load(
            Path(completed_verification) / "invocation.json", case
        )["started_ns"]
    else:
        invocation["verification_started_ns"] = invocation["started_ns"]
    control.save(directory / "invocation.json", invocation)
    sampler, clients, primary, errors = None, {}, None, []
    proof, fences = None, None
    try:
        inventory = cleanup.project_inventory()
        if cleanup.current_volume(inventory) != cleanup.volume_key(metadata["volume"]):
            raise RuntimeError("retirement current volume identity differs")
        groups = cleanup.cases_by_volume()
        stamp = cleanup.fixture_stamp(groups[cleanup.volume_key(metadata["volume"])])
        storage, query = control.service("storage"), control.service("clickhouse")
        native = cleanup.catalog_recoveries(storage)
        if (
            set(native)
            != {item.name for item, _ in groups[cleanup.volume_key(metadata["volume"])]}
            or native[case.name] != snapshot["table_uuid"]
        ):
            raise RuntimeError("retirement native table UUID set differs")
        fences = native_fences(case, base["completed_ns"])
        plan = control.profile(case, storage, query)
        control.save(
            directory / "current-profile.json",
            {**plan, "network_ids": sorted(plan["network_ids"])},
        )
        current_sources = control.sources()
        control.save(directory / "current-sources.json", current_sources)
        if completed_verification is not None:
            proof, current_native, metrics, _ = completed_export(
                case,
                completed_verification,
                plan,
                current_sources,
                storage,
                query,
                stamp["latest_operation_ns"],
            )
            control.save(directory / "resources.json", metrics)
        else:
            sampler = ResourceSampler(directory, env=control.DOCKER_ENV)
            sampler.bind("storage", storage["Id"], "/data")
            sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
            sampler.start()
            control.wait_coverage(
                sampler, ["storage", "clickhouse"], invocation["started_ns"]
            )
            control.tool(
                case,
                "recovery-verify",
                clients,
                sampler,
                plan,
                verification=invocation["verification_id"],
            )
            proof = control.verification_proof(case, invocation["verification_id"])
            control.wait_coverage(
                sampler, ["storage", "clickhouse"], invocation["started_ns"]
            )
        if control.sources() != current_sources:
            raise RuntimeError("retirement sources changed during verification")
    except BaseException as error:
        primary = error
    finally:
        try:
            control.stop_actors(case, clients, sampler)
        except BaseException as error:
            errors.append({"clients": repr(error)})
            primary = primary or error
        if sampler is not None:
            try:
                _, metrics = sampler.close()
                control.save(directory / "resources.json", metrics)
            except BaseException as error:
                errors.append({"resources": repr(error)})
                primary = primary or error
    if primary is None:
        try:
            metadata, base, snapshot, bodies, manifest = original_evidence(
                case, source_manifest, original_manifest
            )
            _check_fences(case, base["completed_ns"], fences)
            _check_corrected(
                case,
                proof,
                metadata,
                snapshot,
                bodies,
                manifest,
                max(
                    stamp["latest_operation_ns"], invocation["verification_started_ns"]
                ),
            )
            current_native, _ = corrected_client(case, proof, plan, current_sources)
            control.save(directory / "current-client.json", current_native)
            for name in (
                "invocation.json",
                "resources.json",
                "current-profile.json",
                "current-sources.json",
                "current-client.json",
            ):
                cleanup.add_artifact(manifest, directory / name, case)
            _completed_artifacts(case, invocation, manifest)
            readonly_fences = readonly_native_fences(case, base["completed_ns"])
            completed_ns = time.time_ns()
            readonly_artifacts(case, manifest, completed_ns)
            receipt = {
                "passed": False,
                "status": STATUS,
                "data_preservation_passed": True,
                **proof,
                "run_id": case.name,
                "retirement_id": identifier,
                "invocation": invocation,
                "volume": metadata["volume"],
                "case_sha256": cleanup.digest(case / "case.json"),
                "source_manifest_sha256": SOURCE_SHA,
                "original_manifest_sha256": ARTIFACT_SHA,
                "native_catalog_body_sha256": CATALOG_SHA,
                "original_process_cutoff_ns": base["completed_ns"],
                "native_fences": fences,
                "readonly_native_fences": readonly_fences,
                "fixture": stamp,
                "artifacts": manifest,
                "completed_ns": completed_ns,
            }
        except BaseException as error:
            primary = error
    if primary is not None:
        receipt = {
            "passed": False,
            "status": "failed-retirement",
            "data_preservation_passed": False,
            "run_id": case.name,
            "retirement_id": identifier,
            "invocation": invocation,
            "error": repr(primary),
            "cleanup_errors": errors,
            "completed_ns": time.time_ns(),
        }
    try:
        control.save(directory / "receipt.json", receipt)
    except BaseException as error:
        if primary is not None:
            primary.add_note(
                "Could not persist failed retirement receipt: " + repr(error)
            )
            raise primary
        raise
    if primary is not None:
        raise primary
    archived_base(case)
    return directory / "receipt.json"
