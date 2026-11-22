"""Interrupt one owned Spark output close; preserve all state on any failure."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import traceback
import uuid

import recovery_control as control
import recovery_cleanup as cleanup
from recovery_resources import ResourceSampler

SPARK = [
    "/opt/spark/bin/spark-submit",
    "--master",
    "local[2]",
    "--driver-memory",
    "1g",
    "--conf",
    "spark.jars.ivy=/tmp/ivy",
    "--conf",
    "spark.driver.extraJavaOptions=-Duser.home=/tmp",
    "/opt/lab/compact.py",
]
WAIT = 'trap "exit 143" TERM INT; while ! test -f "$1"; do sleep 0.1; done; shift; exec "$@"'


def reference(case, path):
    path = cleanup.regular(Path(path), case)
    return {"file": str(path.relative_to(case)), "sha256": cleanup.digest(path)}


def record_failure(case, error):
    """A witness-filesystem error must not replace the original failure."""
    try:
        control.save(
            case / "failure.json",
            {
                "passed": False,
                "error": repr(error),
                "traceback": "".join(traceback.format_exception(error)),
                "native_storage_preserved": True,
            },
        )
    except BaseException as recording:
        error.add_note("Could not persist failure evidence: " + repr(recording))


def helper_sources(value, service, metadata):
    prefix, manifest = (
        ("iceberg-compaction/", "/opt/lab/sources.sha256")
        if service == "recovery-compactor"
        else (
            "compaction-upload-gate/",
            "/usr/local/share/compaction-upload-gate/sources.sha256",
        )
    )
    expected = {
        name: digest
        for name, digest in metadata["source_sha256"].items()
        if name.startswith(prefix)
    }
    actual = {}
    for line in control.call(
        ["docker", "exec", value["Id"], "cat", manifest]
    ).splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.removeprefix("*")
        if "/" in name or prefix + name in actual:
            raise RuntimeError("invalid maintenance source manifest")
        actual[prefix + name] = digest
    if not expected or actual != expected:
        raise RuntimeError("maintenance image sources differ from frozen case")
    return {"sources": actual}


def start_helper(case, role, service, extra, registry, sampler, plan, metadata):
    invocation = uuid.uuid4().hex
    directory = case / "maintenance" / invocation
    directory.mkdir(parents=True)
    name = control.PROJECT + "-" + case.name[:8] + "-" + role + "-" + invocation[:8]
    client = control.Client(role, service, name, invocation, directory, case.name)
    registry[role] = client  # A CLI creation timeout is ambiguous: keep its exact name.
    args = (
        SPARK
        if service == "recovery-compactor"
        else ["/usr/local/bin/upload-gate", "serve"]
    )
    extra = dict(extra)
    if service == "compaction-upload-gate":
        (directory / "gate").mkdir()
        extra["GATE_RESULTS_DIR"] = "/results/maintenance/" + invocation + "/gate"
    else:
        (directory / "spark").mkdir()
        extra["COMPACTION_RESULTS_DIR"] = (
            "/results/maintenance/" + invocation + "/spark"
        )
    control.save(
        directory / "launch.json",
        {
            "role": role,
            "service": service,
            "name": name,
            "command": "compaction"
            if service == "recovery-compactor"
            else "upload-gate",
            "argv": args,
            "run_id": case.name,
            "invocation": invocation,
            "extra": extra,
            "created_host_ns": time.time_ns(),
        },
    )
    command = control.COMPOSE + [
        "run",
        "-d",
        "--no-deps",
        "--pull",
        "never",
        "--name",
        name,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--label",
        "org.examples.recovery.run=" + case.name,
        "--label",
        "org.examples.recovery.invocation=" + invocation,
        "--entrypoint",
        "sh",
    ]
    if service == "compaction-upload-gate":
        command += ["--use-aliases"]
    for key, value in extra.items():
        command += ["-e", key + "=" + str(value)]
    gate = "/results/maintenance/" + invocation + "/START"
    control.call(
        command + [service, "-c", WAIT, "sh", gate, *args],
        env=control.environment(case),
    )
    value = control.inspect_client(client)
    client.identifier, client.initial = value["Id"], value
    control.check_caps(value, service)
    control.check_networks(value, plan["network_ids"])
    control.check_mounts(value, case)
    if (
        not value["State"]["Running"]
        or value["Image"] != plan["images"][service]
        or value["Config"].get("Labels", {}).get("org.examples.owner")
        != control.PROJECT
    ):
        raise RuntimeError("maintenance helper image/ownership differs")
    proof = helper_sources(value, service, metadata)
    control.save(
        directory / "started.json", {**control.state(value), "built_source": proof}
    )
    sampler.bind(role, value["Id"], "/results")
    control.wait_coverage(sampler, [role], time.time_ns())
    control.release(client)
    return client


def end_helper(
    client, sampler, *, stop=False, expected_kill=False, timeout=300, actors=None
):
    deadline = time.monotonic() + timeout
    value = control.inspect_client(client)
    if stop and value["State"]["Running"]:
        control.call(["docker", "kill", "--signal", "SIGTERM", value["Id"]])
    while value["State"]["Running"]:
        if actors:
            control.check_actors(actors)
        if time.monotonic() >= deadline:
            raise TimeoutError("maintenance helper did not finish: " + client.role)
        time.sleep(0.25)
        value = control.inspect_client(client)
    value, _, _ = control.observe_client(client)
    ended = control.retain_end(
        client, value, reason="expected output-close SIGKILL" if expected_kill else None
    )
    sampler.unbind(client.role)
    if not control.fenced_client(value) or value["State"]["OOMKilled"]:
        raise RuntimeError("maintenance helper lacks positive non-OOM fencing")
    if expected_kill:
        if value["State"]["ExitCode"] != 137 or ended["clean"]:
            raise RuntimeError("intentional compactor SIGKILL not proved")
    elif not ended["clean"]:
        raise RuntimeError("maintenance helper failed: " + client.role)
    return value


def gate_status(client):
    value = control.inspect_client(client)
    if not value["State"]["Running"]:
        raise RuntimeError("upload gate exited")
    body = json.loads(
        control.call(
            ["docker", "exec", value["Id"], "/usr/local/bin/upload-gate", "status"]
        )
    )
    if (
        body["run_id"] != client.run_id
        or body.get("fatal_error")
        or body.get("stopping")
    ):
        raise RuntimeError("upload gate status is not healthy/owned")
    return body


def wait_gate(client, actors, *, held=False, timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        control.check_actors(actors)
        try:
            status = gate_status(client)
        except RuntimeError as error:
            # Only startup connection refusal is transient, not an unknown process.
            if "connection refused" not in str(error).lower():
                raise
            status = None
        if status is not None and (not held or status["active_holds"]):
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError("upload gate did not expose durable held output")
        time.sleep(0.25)


def gate_file(case, client, relative):
    root = client.directory / "gate"
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("gate evidence escaped owned invocation")
    return cleanup.regular(root / path, case)


def spark_report(case, client):
    path = client.directory / "spark"
    reports = list(path.glob("compaction-*-resources.json"))
    if len(reports) != 1:
        raise RuntimeError(
            "one successful report required for this exact Spark invocation"
        )
    report = cleanup.load(reports[0], case)
    if report["passed"] is not True or report["stop"].get("passed") is not True:
        raise RuntimeError("Spark report did not complete cleanly")
    return reports[0]


def inspect(case, phase, clients, sampler, plan, *, pin=None, candidate=None):
    identifier = str(uuid.uuid4())
    extra = {"RECOVERY_OBSERVATION_ID": identifier, "RECOVERY_INSPECTION_PHASE": phase}
    if pin is not None:
        extra["RECOVERY_PIN_OBSERVATION"] = pin
    if candidate is not None:
        extra["RECOVERY_CANDIDATE_URI"] = candidate
    control.tool(
        case, "recovery-inspect", clients, sampler, plan, inspection=extra, timeout=360
    )
    path = (
        case
        / ("recovery-" + case.name)
        / "observations"
        / identifier
        / "inspection.json"
    )
    result = cleanup.load(path, case)
    if (
        not result["passed"]
        or result["observation_id"] != identifier
        or result["phase"] != phase
    ):
        raise RuntimeError("read-only inspection did not qualify")
    return path, result


def survivors(storage, query, actors):
    control.check_actors(actors)
    values = {
        "storage": control.owned(storage["Id"], "storage"),
        "query": control.owned(query["Id"], "clickhouse"),
    }
    values |= {name: control.inspect_client(client) for name, client in actors.items()}
    if any(
        control.live_identity(values[role]) != control.live_identity(original)
        for role, original in (("storage", storage), ("query", query))
    ):
        raise RuntimeError("storage/query survivor restarted during compaction")
    if any(
        not v["State"]["Running"] or v["State"]["Pid"] <= 0 for v in values.values()
    ):
        raise RuntimeError("a survivor is not running")
    return {name: control.state(value) for name, value in values.items()}


def replacement_ids(observation):
    return {
        row["snapshot_id"]
        for row in observation["operations"]
        if row["operation"] == "replace"
    }


def compactor_crash():
    context = control.local_docker()
    results = cleanup.results_root()
    base = (control.ROOT / os.environ.get("RESULTS_DIR", "results/manual")).resolve()
    if not base.is_relative_to(results.resolve()) or results.is_symlink():
        raise RuntimeError("RESULTS_DIR must stay in this example's results")
    if shutil.disk_usage(control.ROOT).free < 8 * control.GIB:
        raise RuntimeError("need 8 GiB free before the witnessed compactor case")
    if not 1 <= int(os.environ.get("RECOVERY_ROWS", "2000000")) <= 4000000:
        raise RuntimeError(
            "this bounded compactor case supports at most four million seed rows"
        )
    storage, query = control.service("storage"), control.service("clickhouse")
    if any(
        v["State"].get("Health", {}).get("Status") != "healthy"
        or not v["State"]["Running"]
        for v in (storage, query)
    ):
        raise RuntimeError("healthy owned storage/query required")
    name = control.data_volume(storage)
    (volume,) = json.loads(control.call(["docker", "volume", "inspect", name]))
    if volume.get("Labels", {}).get("com.docker.compose.project") != control.PROJECT:
        raise RuntimeError("foreign storage volume")
    case = base / "production" / str(uuid.uuid4())
    plan = control.profile(case, storage, query, auxiliary=True)
    case.mkdir(parents=True)
    metadata = {
        "kind": "iceberg-compactor-crash",
        "duckdb_version": control.DUCKDB_VERSION,
        "run_id": case.name,
        "project": control.PROJECT,
        "context": context,
        "volume": {key: volume[key] for key in cleanup.VOLUME_FIELDS},
        "storage": control.state(storage),
        "query": control.state(query),
        "source_sha256": control.sources(),
        "created_ns": time.time_ns(),
        "scope": "Compactor-only completed-upload ACK loss with live readers/writers. Not a disk/S3/catalog outage; no orphan cleanup.",
    }
    control.save(case / "case.json", metadata)
    control.save(
        case / "profile.json", plan | {"network_ids": sorted(plan["network_ids"])}
    )
    cleanup.register_fixture(case, metadata)
    sampler = ResourceSampler(case, env=control.DOCKER_ENV)
    sampler.bind("storage", storage["Id"], "/data")
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    sampler.start()
    clients, actors, helpers, errors = {}, {}, {}, []
    primary, proof, envelope = None, None, None
    try:
        control.tool(case, "recovery-seed", clients, sampler, plan)
        workload = case / ("recovery-" + case.name)
        config = cleanup.load(workload / "config.json", case)
        control.phase(case, "baseline")
        for i in range(2):
            role = f"writer-{i:03d}"
            actors[role] = control.start_client(
                case,
                role,
                "recovery-writer",
                "recovery-write",
                {"RECOVERY_WRITER_ID": i},
                clients,
                sampler,
                plan,
            )
            control.release(actors[role])
        for i, engine in enumerate(("duckdb", "clickhouse")):
            role = f"reader-{i:03d}"
            actors[role] = control.start_client(
                case,
                role,
                "recovery-reader",
                "recovery-read",
                {"RECOVERY_READER_ID": i, "RECOVERY_READ_ENGINE": engine},
                clients,
                sampler,
                plan,
            )
            control.release(actors[role])
        control.progress(case, actors, "baseline")
        # The seed record identifies the actual catalog UUID; never guess a path.
        seed = cleanup.load(workload / "seed-complete.json", case)
        if seed["rows"] != config["seed_rows"] or seed[
            "config_sha256"
        ] != cleanup.digest(workload / "config.json"):
            raise RuntimeError("seed completion differs from durable configuration")
        created = cleanup.load(workload / "seed-catalog.json", case)
        if created["available"] is not True:
            raise RuntimeError("seeded native catalog is unavailable")
        table_uuid = created["load_table"]["metadata"]["table-uuid"]
        prefix = f"s3://warehouse/lab/{config['table']}-{table_uuid}/data/"
        spark_extra = {
            "TABLE": "lab." + config["table"],
            "COMPACTION_LIVE_WRITERS": "true",
            "REWRITE_MANIFESTS": "false",
            "COMPACTOR_S3_ENDPOINT": "http://compaction-upload-gate:8333",
        }
        baseline_gate = start_helper(
            case,
            "baseline-gate",
            "compaction-upload-gate",
            {
                "GATE_RUN_ID": case.name,
                "GATE_OBJECT_PREFIX": prefix,
                "GATE_HOLD_FINAL_ACKS": "false",
            },
            helpers,
            sampler,
            plan,
            metadata,
        )
        wait_gate(baseline_gate, actors)

        def spark_settings(endpoint):
            # Each helper's report directory is assigned in start_helper.
            return spark_extra | {"COMPACTOR_S3_ENDPOINT": endpoint}

        baseline_spark = start_helper(
            case,
            "baseline-compactor",
            "recovery-compactor",
            spark_settings("http://compaction-upload-gate:8333"),
            helpers,
            sampler,
            plan,
            metadata,
        )
        end_helper(baseline_spark, sampler, actors=actors)
        baseline_report = spark_report(case, baseline_spark)
        baseline_status = gate_status(baseline_gate)
        forwarded = [
            row
            for row in baseline_status["completed"]
            if row["event"] == "backend-complete-ack-forwarded"
        ]
        if not forwarded:
            raise RuntimeError("healthy baseline never forwarded a target final upload")
        baseline_record = gate_file(case, baseline_gate, forwarded[0]["journal_file"])
        end_helper(baseline_gate, sampler, stop=True, timeout=30, actors=actors)
        before_path, before = inspect(case, "before", clients, sampler, plan)
        pin = before["observation_id"]
        # Continuous actors must create small files after baseline compaction.
        control.hold_outage(actors, 5)
        fault_since = control.phase(case, "fault")
        gate = start_helper(
            case,
            "upload-gate",
            "compaction-upload-gate",
            {
                "GATE_RUN_ID": case.name,
                "GATE_OBJECT_PREFIX": prefix,
                "GATE_HOLD_FINAL_ACKS": "true",
            },
            helpers,
            sampler,
            plan,
            metadata,
        )
        wait_gate(gate, actors)
        fault = start_helper(
            case,
            "fault-compactor",
            "recovery-compactor",
            spark_settings("http://compaction-upload-gate:8333"),
            helpers,
            sampler,
            plan,
            metadata,
        )
        status = wait_gate(gate, actors, held=True)
        control.save(case / "gate-status-initial.json", status)
        record = status["active_holds"][0]
        record_path = gate_file(case, gate, record["journal_file"])
        candidate_path, candidate = inspect(
            case,
            "candidate",
            clients,
            sampler,
            plan,
            pin=pin,
            candidate=record["object_uri"],
        )
        if replacement_ids(candidate) != replacement_ids(before):
            raise RuntimeError("a replacement committed before the held-output fault")
        control.progress(case, actors, "fault")
        control.wait_coverage(
            sampler,
            ["storage", "clickhouse", *actors, gate.role, fault.role],
            fault_since,
            actors,
        )
        control.save(case / "survivor-before.json", survivors(storage, query, actors))
        value = control.inspect_client(fault)
        if not value["State"]["Running"] or control.live_identity(
            value
        ) != control.live_identity(fault.initial):
            raise RuntimeError(
                "intended fault compactor is not its original live process"
            )
        control.save(case / "compactor-before-kill.json", control.state(value))
        # A timeout/retry may cancel the original upload while inspection runs.
        # Do not substitute a different held request or assume the old one lives.
        latest_status = gate_status(gate)
        if record not in latest_status["active_holds"]:
            raise RuntimeError(
                "the inspected upload is no longer actively held before SIGKILL"
            )
        control.save(
            case / "gate-status.json",
            latest_status | {"observed_host_ns": time.time_ns()},
        )
        fault_started = time.time_ns()
        control.call(["docker", "kill", "--signal", "SIGKILL", value["Id"]])
        fault.forced = True
        after = end_helper(
            fault, sampler, expected_kill=True, timeout=30, actors=actors
        )
        fault_completed = time.time_ns()
        control.save(case / "compactor-after-kill.json", control.state(after))
        control.hold_outage(actors, 10)
        interrupted_path, interrupted = inspect(
            case, "interrupted", clients, sampler, plan, pin=pin
        )
        if replacement_ids(interrupted) != replacement_ids(before):
            raise RuntimeError(
                "interrupted compaction commit is ambiguous or unexpectedly applied"
            )
        control.save(case / "survivor-after.json", survivors(storage, query, actors))
        control.phase(case, "recovery")
        end_helper(gate, sampler, stop=True, timeout=30, actors=actors)
        recovered = start_helper(
            case,
            "recovered-compactor",
            "recovery-compactor",
            spark_settings("http://storage:8333"),
            helpers,
            sampler,
            plan,
            metadata,
        )
        end_helper(recovered, sampler, actors=actors)
        recovered_report = spark_report(case, recovered)
        control.phase(case, "recovered")
        recovered_path, recovered_observation = inspect(
            case, "recovered", clients, sampler, plan, pin=pin
        )
        if not replacement_ids(recovered_observation) - replacement_ids(before):
            raise RuntimeError("recovery did not commit a new data replacement")
        control.progress(case, actors, "recovered")
        control.hold_outage(actors, 5)
        control.save(case / "survivor-final.json", survivors(storage, query, actors))
        control.phase(case, "stopping")
        control.stop_actors(case, actors, sampler)
        verification = str(uuid.uuid4())
        control.tool(
            case, "recovery-verify", clients, sampler, plan, verification=verification
        )
        proof = control.verification_proof(case, verification)
        final_services = {}
        for role, original in (("storage", storage), ("query", query)):
            current = control.owned(
                original["Id"], "storage" if role == "storage" else "clickhouse"
            )
            if control.live_identity(current) != control.live_identity(original):
                raise RuntimeError(
                    "storage/query survivor changed during final verification"
                )
            final_services[role] = control.state(current)
        control.save(case / "service-survivor-final.json", final_services)
        envelope = {
            "version": 1,
            "run_id": case.name,
            "config_sha256": cleanup.digest(workload / "config.json"),
            "fault_invocation": fault.invocation,
            "recovery_invocation": recovered.invocation,
            "gate_invocation": gate.invocation,
            "baseline_gate_invocation": baseline_gate.invocation,
            "baseline_compactor_invocation": baseline_spark.invocation,
            "fault_started_ns": fault_started,
            "fault_completed_ns": fault_completed,
            "fault_before": reference(case, case / "compactor-before-kill.json"),
            "fault_after": reference(case, case / "compactor-after-kill.json"),
            "survivor_before": reference(case, case / "survivor-before.json"),
            "survivor_after": reference(case, case / "survivor-after.json"),
            "survivor_final": reference(case, case / "survivor-final.json"),
            "service_survivor_final": reference(
                case, case / "service-survivor-final.json"
            ),
            "gate_status": reference(case, case / "gate-status.json"),
            "gate_record": reference(case, record_path),
            "baseline_gate_record": reference(case, baseline_record),
            "gate_config": reference(
                case,
                gate.directory / "gate" / ("upload-gate-" + case.name) / "config.json",
            ),
            "baseline_gate_config": reference(
                case,
                baseline_gate.directory
                / "gate"
                / ("upload-gate-" + case.name)
                / "config.json",
            ),
            "baseline_report": reference(case, baseline_report),
            "recovered_report": reference(case, recovered_report),
            "inspections": {
                name: reference(case, path)
                for name, path in zip(
                    ("before", "candidate", "interrupted", "recovered"),
                    (before_path, candidate_path, interrupted_path, recovered_path),
                )
            },
        }
    except BaseException as error:
        primary = error
        record_failure(case, error)
    finally:
        for helper in reversed(list(helpers.values())):
            if (helper.directory / "ended.json").exists():
                continue
            try:
                value = control.abort_client(helper)
                control.observe_client(helper)
                control.retain_end(helper, value, reason="controller failure cleanup")
                sampler.unbind(helper.role)
            except BaseException as error:
                errors.append({"helper": helper.role, "error": repr(error)})
        try:
            control.stop_actors(case, clients, sampler)
        except BaseException as error:
            errors.append({"clients": repr(error)})
        try:
            control.phase(case, "finished")
        except BaseException as error:
            errors.append({"finished_phase": repr(error)})
        try:
            control.finish_metrics(case, sampler)
        except BaseException as error:
            errors.append({"metrics": repr(error)})
    if errors:
        try:
            control.save(case / "cleanup-errors.json", errors)
        except BaseException as recording:
            if primary is None:
                primary = recording
            else:
                primary.add_note("Could not persist cleanup errors: " + repr(recording))
    if primary is not None:
        raise primary
    if errors:
        raise RuntimeError("compactor cleanup/metrics did not complete")
    if control.sources() != metadata["source_sha256"]:
        error = RuntimeError("operational sources changed during compactor test")
        record_failure(case, error)
        raise error
    control.save(case / "compactor-evidence.json", envelope)
    result = {
        "passed": True,
        **proof,
        "compactor_evidence": reference(case, case / "compactor-evidence.json"),
        "completed_ns": time.time_ns(),
    }
    from compactor_evidence import check_compactor_case

    try:
        check_compactor_case(case, metadata, result, {})
    except BaseException as error:
        record_failure(case, error)
        raise
    control.save(case / "result.json", result)
    print(str(case.relative_to(control.ROOT)), flush=True)


def main():
    if sys.version_info < (3, 11):
        raise RuntimeError("compactor controller requires Python 3.11 or newer")
    parser = argparse.ArgumentParser()
    parser.parse_args()
    signal.signal(signal.SIGTERM, control.interrupt_controller)
    results = cleanup.results_root()
    results.mkdir(exist_ok=True)
    descriptor = os.open(
        results / "controller.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644
    )
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        compactor_crash()


if __name__ == "__main__":
    main()
