"""Read-only, witnessed S3 manifest connection-close regression; never repairs data."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time
from urllib.parse import urlsplit
import uuid

import recovery_cleanup as cleanup
import recovery_control as control
from recovery_resources import ResourceSampler


def source_fixture(path, verification=None, inspection=None):
    path = Path(path)
    if ".." in path.parts:
        raise RuntimeError("case path contains traversal")
    case = cleanup.regular(
        path if path.is_absolute() else control.ROOT / path, cleanup.results_root()
    ).resolve()
    registered = {
        p.resolve(): metadata
        for group in cleanup.cases_by_volume().values()
        for p, metadata in group
    }
    if case not in registered:
        raise RuntimeError("manifest probe requires an exact registered recovery case")
    metadata, artifacts = registered[case], {}
    workload = case / ("recovery-" + case.name)
    config = cleanup.load(workload / "config.json", case)
    checksum = cleanup.digest(workload / "config.json")
    if (
        config["version"] != 1
        or config["run_id"] != cleanup.valid_uuid(case.name)
        or config["table"] != "recovery_" + uuid.UUID(case.name).hex
    ):
        raise RuntimeError("source configuration identity differs")
    if verification is not None and inspection is not None:
        raise RuntimeError("select one verification or before inspection")
    if inspection is not None:
        identifier = cleanup.valid_uuid(inspection)
        proof_path = workload / "observations" / identifier / "inspection.json"
        proof = cleanup.load(proof_path, case)
        pin = proof["pin"]
        snapshot, rows = pin["snapshot"], pin["proof"]["rows"]
        raw_path = proof_path.parent / "catalog.json"
        raw = cleanup.load(raw_path, case)
        native = raw["load_table"]
        if (
            proof["passed"] is not True
            or proof.get("error")
            or proof["phase"] != "before"
            or proof["observation_id"] != identifier
            or pin["observation_id"] != identifier
            or snapshot != proof["catalog"]
            or pin["proof"]["seed_rows"] != config["seed_rows"]
            or not re.fullmatch(r"[0-9a-f]{64}", pin["proof"]["body_sha256"])
        ):
            raise RuntimeError(
                "source before inspection lacks a complete pinned-body proof"
            )
        if (
            raw["available"] is not True
            or raw["run_id"] != case.name
            or raw["config_sha256"] != checksum
            or native["metadata-location"] != snapshot["metadata_location"]
            or native["metadata"]["table-uuid"] != snapshot["table_uuid"]
            or native["metadata"]["current-snapshot-id"] != snapshot["snapshot_id"]
            or snapshot["snapshot_id"]
            not in {s["snapshot-id"] for s in native["metadata"]["snapshots"]}
        ):
            raise RuntimeError("inspection native catalog identity differs")
        cleanup.add_artifact(artifacts, raw_path, case)
    else:
        result = None
        if verification is None:
            result = cleanup.load(case / "result.json", case)
            if result["passed"] is not True:
                raise RuntimeError("default source result must be successful")
            verification = result["verification_id"]
            cleanup.add_artifact(artifacts, case / "result.json", case)
        identifier = cleanup.valid_uuid(verification)
        proof_path = workload / "verifications" / identifier / "verified.json"
        reference = {
            "verification_id": identifier,
            "verification_sha256": cleanup.digest(cleanup.regular(proof_path, case)),
        }
        if (
            result is not None
            and reference["verification_sha256"] != result["verification_sha256"]
        ):
            raise RuntimeError("default source verification bytes changed")
        proof = cleanup.check_verification(case, reference, metadata, artifacts, 0)
        snapshot, rows = (
            proof["snapshot"],
            proof["exports"]["duckdb"]["proof"]["rows_verified"],
        )
    if proof["run_id"] != case.name or proof["config_sha256"] != checksum:
        raise RuntimeError("source proof/configuration mismatch")
    prefix = (
        f"/lab/{config['table']}-{cleanup.valid_uuid(snapshot['table_uuid'])}/metadata/"
    )
    uri = urlsplit(snapshot["metadata_location"])
    if (
        uri.scheme != "s3"
        or uri.netloc != "warehouse"
        or uri.query
        or uri.fragment
        or "?" in snapshot["metadata_location"]
        or "#" in snapshot["metadata_location"]
        or not uri.path.startswith(prefix)
        or not uri.path.endswith(".metadata.json")
        or any(p in {".", ".."} for p in uri.path.split("/"))
        or any(c in uri.path for c in "%\\\n\r")
        or type(snapshot["snapshot_id"]) is not int
        or not 0 < snapshot["snapshot_id"] < 2**63
    ):
        raise RuntimeError("snapshot is outside the exact owned table")
    if (
        any(
            type(n) is not int or n <= 0 or n % 10000
            for n in (rows, config["seed_rows"], config["batch_rows"])
        )
        or rows < config["seed_rows"]
    ):
        raise RuntimeError("checksum requires complete aligned 10000-row cycles")
    for item in (case / "case.json", workload / "config.json", proof_path):
        cleanup.add_artifact(artifacts, item, case)
    return case, metadata, config, snapshot, rows, artifacts


def active_fixture(metadata):
    storage, query = control.service("storage"), control.service("clickhouse")
    if any(
        not v["State"]["Running"]
        or v["State"]["OOMKilled"]
        or v["State"].get("Health", {}).get("Status") != "healthy"
        for v in (storage, query)
    ):
        raise RuntimeError("healthy owned storage/query required")
    name = control.data_volume(storage)
    (volume,) = json.loads(control.call(["docker", "volume", "inspect", name]))
    if (
        cleanup.volume_key(volume) != cleanup.volume_key(metadata["volume"])
        or volume.get("Labels", {}).get("com.docker.compose.project") != control.PROJECT
    ):
        raise RuntimeError("active native storage volume differs from source fixture")
    return storage, query


def survivors(storage, query):
    result = {}
    for role, original, service in (
        ("storage", storage, "storage"),
        ("query", query, "clickhouse"),
    ):
        current = control.owned(original["Id"], service)
        if (
            control.live_identity(current) != control.live_identity(original)
            or current["Image"] != original["Image"]
            or not current["State"]["Running"]
            or current["State"]["OOMKilled"]
        ):
            raise RuntimeError("unrelated storage/query survivor changed")
        result[role] = control.state(current)
    return result


def outcome(mode, state, report, requests, stderr, rows, metadata_prefix):
    if state["Running"] or state["Pid"] != 0 or state["OOMKilled"]:
        raise RuntimeError("probe is unfenced or OOM-killed")
    code = state["ExitCode"]
    closed = [
        r
        for r in requests
        if r["action"] == "manifest-connection-closed" and not r.get("error")
    ]
    if mode == "healthy":
        if (
            code != 0
            or not report
            or report["passed"] is not True
            or report["dropped_requests"] != 0
            or closed
            or "SIGABRT" in stderr
            or report["rows"] != rows
            or report["amount_sum"] != rows // 10000 * 50005000
        ):
            raise RuntimeError("healthy manifest read/checksum failed")
        return {
            "passed": True,
            "native_query_passed": True,
            "backend_contract_passed": True,
        }
    intents = [r for r in requests if r["action"] == "manifest-connection-close-intent"]
    if not closed:
        raise RuntimeError("fsynced manifest connection-close evidence missing")
    for record in closed:
        uri = urlsplit(record["request_uri"])
        matched = any(
            i["request_uri"] == record["request_uri"]
            and i["signed_host"] == record["signed_host"]
            and 0 < i["ns"] <= record["ns"]
            for i in intents
        )
        if (
            record["method"] != "GET"
            or uri.scheme
            or uri.netloc
            or not uri.path.startswith(metadata_prefix)
            or not uri.path.endswith(".avro")
            or uri.path.removeprefix(metadata_prefix).startswith("snap-")
            or not matched
        ):
            raise RuntimeError("fsynced manifest connection-close evidence missing")
    if (
        code == 2
        and report is None
        and "SIGABRT" in stderr
        and "Server returned nothing" in stderr
    ):
        return {
            "passed": False,
            "native_query_passed": False,
            "backend_contract_passed": False,
            "reproduced_native_abort": True,
        }
    if (
        code != 1
        or not report
        or report["passed"] is not False
        or report["dropped_requests"] < 1
        or "SIGABRT" in stderr
        or "Server returned nothing" not in report.get("error", "")
        or not any(urlsplit(r["request_uri"]).path in report["error"] for r in closed)
    ):
        raise RuntimeError("fault produced an unexpected success/error/exit")
    return {
        "passed": True,
        "native_query_passed": False,
        "backend_contract_passed": True,
        "expected_sql_error": True,
    }


def run_probe(
    case, mode, config, snapshot, rows, clients, sampler, plan, storage, query
):
    workload = case / ("recovery-" + case.name)
    previous = set(workload.glob("manifest-probe-*"))
    role = "manifest-" + mode
    client = control.start_client(
        case,
        role,
        "recovery-tools",
        "recovery-manifest-probe",
        {
            "RECOVERY_MANIFEST_MODE": mode,
            "RECOVERY_PROBE_METADATA": snapshot["metadata_location"],
            "RECOVERY_PROBE_SNAPSHOT": snapshot["snapshot_id"],
            "RECOVERY_PROBE_TABLE_UUID": snapshot["table_uuid"],
        },
        clients,
        sampler,
        plan,
    )
    control.wait_coverage(sampler, ["storage", "clickhouse", role], time.time_ns())
    control.release(client)
    deadline = time.monotonic() + config["verification_timeout_seconds"] + 30
    while True:
        value = control.inspect_client(client)
        if not value["State"]["Running"]:
            break
        if time.monotonic() > deadline:
            raise TimeoutError("native manifest probe did not end")
        time.sleep(0.25)
    value, _, stderr = control.observe_client(client)
    ended = control.retain_end(client, value)
    sampler.unbind(role)
    if (
        client.forced
        or client.aborted
        or value["State"]["StartedAt"] != client.initial["State"]["StartedAt"]
        or value["RestartCount"] != client.initial["RestartCount"]
    ):
        raise RuntimeError("probe process was replaced or externally stopped")
    fresh = set(workload.glob("manifest-probe-*")) - previous
    if len(fresh) != 1:
        raise RuntimeError("exactly one native probe directory required")
    directory = cleanup.regular(fresh.pop(), case)
    started = cleanup.load(directory / "started.json", case)
    native = cleanup.load(directory / "native-version.json", case)
    report = (
        cleanup.load(directory / "result.json", case)
        if (directory / "result.json").exists()
        else None
    )
    for record in (started, *([report] if report else [])):
        if (
            record["run_id"] != case.name
            or record["config_sha256"] != cleanup.digest(workload / "config.json")
            or record["mode"] != mode
            or record["snapshot"] != snapshot
        ):
            raise RuntimeError("native probe report identity differs")
    if native != {"native_engine_version": "v" + control.DUCKDB_VERSION} or (
        report and report["native_engine_version"] != native["native_engine_version"]
    ):
        raise RuntimeError("actual native engine differs from selected lane")
    requests_path = cleanup.regular(directory / "requests.jsonl", case)
    if not requests_path.read_bytes().endswith(b"\n"):
        raise RuntimeError("native request witness is empty/torn")
    prefix = (
        "/warehouse"
        + urlsplit(snapshot["metadata_location"]).path.rsplit("/", 1)[0]
        + "/"
    )
    result = outcome(
        mode,
        value["State"],
        report,
        control.history(requests_path),
        stderr,
        rows,
        prefix,
    )
    return {
        "mode": mode,
        "engine": native["native_engine_version"],
        "native_exit": ended,
        "probe": str(directory.relative_to(case)),
        "survivors": survivors(storage, query),
        **result,
    }


def run(path, verification=None, inspection=None):
    control.local_docker()
    source, metadata, config, snapshot, rows, artifacts = source_fixture(
        path, verification, inspection
    )
    storage, query = active_fixture(metadata)  # Before launching any helper.
    case = cleanup.regular(
        cleanup.results_root() / "manifest-io" / str(uuid.uuid4()) / source.name,
        cleanup.results_root(),
    )
    plan = control.profile(case, storage, query)
    workload = case / ("recovery-" + case.name)
    workload.mkdir(parents=True)
    control.save_text(
        workload / "config.json",
        (source / ("recovery-" + source.name) / "config.json")
        .read_bytes()
        .decode("utf-8"),
    )
    if cleanup.digest(workload / "config.json") != cleanup.digest(
        source / ("recovery-" + source.name) / "config.json"
    ):
        raise RuntimeError("copied configuration bytes differ")
    frozen = control.sources()
    control.save(
        case / "invocation.json",
        {
            "source_case": str(source.relative_to(control.ROOT)),
            "source_artifacts": artifacts,
            "source_sha256": frozen,
            "snapshot": snapshot,
            "known_rows": rows,
            "engine": control.DUCKDB_VERSION,
            "scope": "Read-only count/checksum and manifest-fault diagnostic; not capacity or complete production qualification.",
        },
    )
    control.save(
        case / "profile.json", plan | {"network_ids": sorted(plan["network_ids"])}
    )
    sampler = ResourceSampler(case, env=control.DOCKER_ENV)
    sampler.bind("storage", storage["Id"], "/data")
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    clients, results, failure, errors = {}, [], None, []
    try:
        sampler.start()
        for mode in ("healthy", "drop-data-manifest"):
            results.append(
                run_probe(
                    case,
                    mode,
                    config,
                    snapshot,
                    rows,
                    clients,
                    sampler,
                    plan,
                    storage,
                    query,
                )
            )
    except BaseException as error:
        failure = error
    finally:
        for client in clients.values():
            if not (client.directory / "ended.json").exists():
                try:
                    value = control.abort_client(client)
                    control.observe_client(client)
                    control.retain_end(client, value, reason="probe failure cleanup")
                except BaseException as error:
                    errors.append({"client": client.role, "error": repr(error)})
                    failure = failure or error
        try:
            _, resources = sampler.close()
            control.save(case / "resource-summary.json", resources)
        except BaseException as error:
            errors.append({"resources": repr(error)})
            failure = failure or error
    try:
        if control.sources() != frozen or any(
            cleanup.digest(cleanup.regular(source / p, source)) != checksum
            for p, checksum in artifacts.items()
        ):
            raise RuntimeError("source/controller evidence changed during probe")
    except BaseException as error:
        errors.append({"source_freshness": repr(error)})
        failure = failure or error
    passed = failure is None and len(results) == 2 and all(r["passed"] for r in results)
    control.save(
        case / "result.json",
        {
            "passed": passed,
            "results": results,
            "error": repr(failure) if failure else None,
            "cleanup_errors": errors,
            "reproduced_native_abort": any(
                r.get("reproduced_native_abort") for r in results
            ),
            "completed_ns": time.time_ns(),
        },
    )
    print(case.relative_to(control.ROOT), flush=True)
    if not passed:
        raise failure or RuntimeError(
            "native abort fails the backend error-handling contract"
        )
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--verification-id")
    selector.add_argument("--inspection-id")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, control.interrupt_controller)
    results = cleanup.results_root()
    results.mkdir(exist_ok=True)
    descriptor = os.open(
        results / "controller.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644
    )
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.case, args.verification_id, args.inspection_id)


if __name__ == "__main__":
    main()
