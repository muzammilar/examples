"""Serial, example-owned PostgreSQL fault controller. Never resets failed evidence."""

import datetime
import fcntl
import hashlib
import json
import shutil

from replica_proofs import audit_copies
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "pgvector-product-search"
MODE = os.environ.get("MODE", "cluster")
if MODE not in ("single", "cluster"):
    raise ValueError("MODE must be single or cluster")
NODES = ["single"] if MODE == "single" else ["pg0", "pg1", "pg2"]
SERVICES = NODES if MODE == "single" else ["etcd0", "etcd1", "etcd2"] + NODES
RESULTS = (ROOT / os.environ.get("RESULTS_DIR", "results/production/" + MODE)).resolve()
if not RESULTS.is_relative_to(ROOT / "results"):
    raise ValueError("results must be owned by this example")
EMPTY_VOLUME_CHECK = 'entries=$(ls -A /var/lib/postgresql/patroni); test -z "$entries"; test ! -e /var/lib/postgresql/patroni/data/PG_VERSION; echo EMPTY_OWNED_VOLUME'
COMPOSE = [
    "docker",
    "compose",
    "--file",
    str(ROOT / "docker-compose.yml"),
    "--project-name",
    PROJECT,
    "--profile",
    MODE,
]
ENV = os.environ | {
    "COMPOSE_FILE": str(ROOT / "docker-compose.yml"),
    "COMPOSE_PROJECT_NAME": PROJECT,
    "MODE": MODE,
    "PG_NODES": ",".join(NODES),
    "RESULTS_DIR": str(RESULTS.relative_to(ROOT)),
}


def call(args, timeout=300, env=None):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env or ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"{args}: exit {result.returncode}: {result.stdout[-6000:]}")
    return result.stdout


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def records(path):
    if not path.exists():
        return []
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.endswith("\n")]


def inspect_id(identity):
    rows = json.loads(call(["docker", "inspect", identity], timeout=30))
    if len(rows) != 1:
        raise RuntimeError("ambiguous container inspection")
    row = rows[0]
    if (
        row["Id"] != identity
        or row["Config"]["Labels"].get("com.docker.compose.project") != PROJECT
    ):
        raise RuntimeError("container ownership/identity mismatch")
    return row


def mount_records(container):
    result = {}
    for mount in container["Mounts"]:
        key = (mount["Type"], mount["Destination"])
        if key in result:
            raise RuntimeError("duplicate mount destination/type")
        result[key] = mount
    return result


def containers():
    output = call(COMPOSE + ["ps", "--all", "--quiet"] + SERVICES, timeout=30).split()
    result = {}
    for identity in output:
        row = inspect_id(identity)
        service = row["Config"]["Labels"]["com.docker.compose.service"]
        if service not in SERVICES or service in result:
            raise RuntimeError("unexpected/duplicate owned service")
        result[service] = row
    if set(result) != set(SERVICES):
        raise RuntimeError("incomplete owned infrastructure")
    return result


def command_result(action):
    path = RESULTS / ("command-" + action + ".json")
    if path.exists():
        archive = (
            RESULTS / "command-history" / (str(time.time_ns()) + "-" + action + ".json")
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        path.rename(archive)
    return path


def tools(action, environment=None):
    path = command_result(action)
    arguments = COMPOSE + [
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--entrypoint",
        "python",
    ]
    for key, value in (environment or {}).items():
        arguments += ["-e", key + "=" + value]
    output = call(
        arguments + ["tools", "/app/production.py", action],
        env=ENV | (environment or {}),
        timeout=900,
    )
    if not path.exists():
        raise RuntimeError(
            "native command did not write a fresh result: " + output[-3000:]
        )
    result = json.loads(path.read_text())
    expected_verifier = hashlib.sha256(
        (ROOT / "app/production.py").read_bytes()
    ).hexdigest()
    if action == "healthy":
        barrier = result[primary(result)].get("recovery_barrier", {})
        if barrier.get("verifier_source_sha256") != expected_verifier:
            raise RuntimeError(
                "tools image lacks the current live replay gate; run make build before faults"
            )
    if isinstance(result, dict) and result.get("scope") in {
        "all-native-copies",
        "rebuilt-member-live-snapshot",
    }:
        if result.get("verifier_source_sha256") != expected_verifier:
            raise RuntimeError(
                "tools verifier source differs from this checkout; run make build"
            )
        documents = json.loads(
            (ROOT / "results/corpus/prepared/manifest.json").read_text()
        )["parameters"]["documents"]
        proof = audit_copies(result, RESULTS, NODES, documents)
        save(RESULTS / ("copy-proof-audit-" + str(time.time_ns()) + ".json"), proof)
        result["copy_proof_audit"] = proof
    return result


def wait(check, description, timeout=120):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            result = check()
            if result:
                return result
        except (FileNotFoundError, json.JSONDecodeError) as err:
            last = err
        time.sleep(1)
    raise RuntimeError(description + " timed out: " + str(last))


def primary(native):
    candidates = [n for n in NODES if native[n].get("role") == "primary"]
    if len(candidates) != 1:
        raise RuntimeError("native primary not unique")
    return candidates[0]


def window(events, start, end):
    selected = [e for e in events if start <= e["time"] < end]
    by_stage = {}
    for event in selected:
        stage = event["stage"]
        item = by_stage.setdefault(stage, {"requests": 0, "rows": 0, "latency_ms": []})
        item["requests"] += 1
        item["rows"] += len(event.get("rows", []))
        if "seconds" in event:
            item["latency_ms"].append(event["seconds"] * 1000)
    for item in by_stage.values():
        values = sorted(item.pop("latency_ms"))
        if values:
            import math

            item.update(
                p50_ms=values[math.ceil(len(values) * 0.5) - 1],
                p99_ms=values[math.ceil(len(values) * 0.99) - 1],
                max_ms=values[-1],
                latency_samples=len(values),
            )
        item["requests_per_second"] = item["requests"] / max(end - start, 0.000001)
        item["rows_per_second"] = item["rows"] / max(end - start, 0.000001)
    return by_stage


def prove_progress(case, since, processes, successes=3):
    for role, process in processes.items():
        if process.poll() is not None:
            raise RuntimeError(
                role + " exited during recovery; inspect its retained log"
            )
    writes = [
        e
        for e in records(case / "writer.jsonl")
        if e["stage"] == "ack"
        and e.get("rows")
        and e["time"] - e.get("seconds", 0) >= since
    ]
    reads = records(case / "reader.jsonl")
    if any(e["stage"] == "wrong" for e in reads):
        raise RuntimeError("reader detected wrong data")
    return len(writes) >= successes and all(
        sum(
            e["stage"] == kind and e["time"] - e.get("seconds", 0) >= since
            for e in reads
        )
        >= successes
        for kind in ["get", "search"]
    )


def fenced(identity):
    row = inspect_id(identity)
    if (
        row["State"]["Running"]
        or row["State"]["ExitCode"] != 137
        or row["State"]["OOMKilled"]
    ):
        raise RuntimeError("SIGKILL fencing not established")
    return row


def prove_survivors(before, excluded, native_before):
    result = containers()
    for service in set(SERVICES) - set(excluded):
        old, new = before[service], result[service]
        if (old["Id"], old["State"]["StartedAt"]) != (
            new["Id"],
            new["State"]["StartedAt"],
        ) or not new["State"]["Running"]:
            raise RuntimeError("unrelated container restarted: " + service)
    native = tools("state")
    for service in set(NODES) - set(excluded):
        if (
            native[service].get("postmaster_started")
            != native_before[service]["postmaster_started"]
        ):
            raise RuntimeError("unrelated PostgreSQL postmaster restarted: " + service)
    return result, native


def replace_disk(target, identity, case):
    old = fenced(identity)
    mounts = [
        m
        for m in old["Mounts"]
        if m["Type"] == "volume" and m["Destination"] == "/var/lib/postgresql/patroni"
    ]
    if len(mounts) != 1:
        raise RuntimeError("unexpected data volume layout")
    name = mounts[0]["Name"]
    volume = json.loads(call(["docker", "volume", "inspect", name]))[0]
    labels = volume.get("Labels") or {}
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.volume") != target
    ):
        raise RuntimeError("volume ownership mismatch")
    save(case / "old-volume.json", volume)
    call(["docker", "rm", "--volumes", identity])
    # The healthy runtime must positively enumerate absence before storage deletion.
    if identity in call(["docker", "ps", "--all", "--no-trunc", "--quiet"]).split():
        raise RuntimeError("old owner still exists")
    call(["docker", "volume", "rm", name])
    provision_replacement(target, identity, volume, case)


def provision_replacement(target, identity, volume, case):
    name = volume["Name"]
    labels = volume.get("Labels") or {}
    if (
        target not in NODES
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.volume") != target
    ):
        raise RuntimeError("replacement ownership record invalid")
    if identity in call(["docker", "ps", "--all", "--no-trunc", "--quiet"]).split():
        raise RuntimeError("old owner still exists; refusing replacement")
    names = call(["docker", "volume", "ls", "--quiet"]).split()
    if name in names:
        existing = json.loads(call(["docker", "volume", "inspect", name]))[0]
        existing_labels = existing.get("Labels") or {}
        if (
            existing_labels.get("com.docker.compose.project") != PROJECT
            or existing_labels.get("com.docker.compose.volume") != target
        ):
            raise RuntimeError(
                "existing replacement volume is not owned; refuse to attach it"
            )
        if existing["CreatedAt"] == volume["CreatedAt"]:
            raise RuntimeError("original volume still exists; do not overwrite it")
    call(COMPOSE + ["create", "--no-recreate", target])
    replacement = containers()[target]
    if replacement["State"]["Running"]:
        raise RuntimeError(
            "replacement already running; inspect it instead of declaring an empty disk"
        )
    fresh = json.loads(call(["docker", "volume", "inspect", name]))[0]
    fresh_labels = fresh.get("Labels") or {}
    if (
        fresh_labels.get("com.docker.compose.project") != PROJECT
        or fresh_labels.get("com.docker.compose.volume") != target
    ):
        raise RuntimeError("replacement volume labels differ")
    if fresh["CreatedAt"] == volume["CreatedAt"] or replacement["Id"] == identity:
        raise RuntimeError("replacement volume/container identity did not change")
    empty = call(
        COMPOSE
        + [
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "/bin/sh",
            target,
            "-ec",
            EMPTY_VOLUME_CHECK,
        ]
    )
    if "EMPTY_OWNED_VOLUME" not in empty:
        raise RuntimeError("new volume was not proven empty")
    save(case / "new-volume.json", fresh)
    call(COMPOSE + ["start", target])


def experiment(action):
    if action not in {
        "correctness",
        "primary-crash",
        "replica-crash",
        "disk-failure",
        "rebuilt-crash",
        "cold-reopen",
        "crash-reopen",
    }:
        raise ValueError("unknown production case: " + action)
    if (
        action in ("primary-crash", "replica-crash", "disk-failure", "rebuilt-crash")
        and MODE != "cluster"
    ):
        raise RuntimeError("this role test requires cluster mode")
    RESULTS.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < 4 * 1024**3:
        raise RuntimeError(
            "fewer than 4 GiB free; preserve evidence and make room before further faults"
        )
    before = containers()
    native_before = tools("healthy")
    if action=='cold-reopen':
        expected='SIGINT' if MODE=='single' else 'SIGTERM'
        configured={n:before[n]['Config'].get('StopSignal') for n in NODES}
        if any(value!=expected for value in configured.values()):
            raise RuntimeError('wrong image stop signal; use a rebuilt data-plane lifecycle: '+repr(configured))
    history = (
        sorted((RESULTS / "cases").glob("*/writer.jsonl"))
        if (RESULTS / "cases").exists()
        else []
    )
    version = 1
    for file in history:
        for event in records(file):
            if event["stage"] == "attempt":
                version = max(version, 1 + max(row["version"] for row in event["rows"]))
    case = RESULTS / "cases" / uuid.uuid4().hex
    case.mkdir(parents=True)
    save(
        case / "case.json",
        {
            "action": action,
            "start_version": version,
            "started": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "mode": MODE,
        },
    )
    save(case / "history.json", [str(p.parent.relative_to(RESULTS)) for p in history])
    source_hashes = {}
    for source in ROOT.rglob("*"):
        if source.is_file() and not any(
            part in {"results", ".build", "__pycache__"}
            for part in source.relative_to(ROOT).parts
        ):
            relative = source.relative_to(ROOT)
            body = source.read_bytes()
            source_hashes[str(relative)] = hashlib.sha256(body).hexdigest()
            snapshot = case / "source" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(body)
    save(case / "source-sha256.json", source_hashes)
    save(case / "containers-before.json", before)
    save(case / "native-before.json", native_before)
    processes, logs, markers, restore = {}, [], {}, []

    def mark(name):
        markers[name] = time.time()
        save(case / ("phase-" + name + ".json"), {"phase": name, "time": markers[name]})
        print(
            name, datetime.datetime.now(datetime.timezone.utc).isoformat(), flush=True
        )

    def stop(role):
        (case / ("stop-" + role)).touch()
        process = processes[role]
        code = process.wait(timeout=30)
        if code:
            raise RuntimeError(
                role + " failed: " + (case / (role + ".log")).read_text()[-4000:]
            )
        summary = json.loads((case / (role + ".json")).read_text())
        if not summary.get("stopped_by_controller"):
            raise RuntimeError(role + " did not survive until requested stop")
        return summary

    report = {"action": action, "case": str(case.relative_to(ROOT)), "passed": False}
    try:
        for role in ["writer", "reader"]:
            log = (case / (role + ".log")).open("x")
            logs.append(log)
            env = ENV | {"CASE_DIR": str(case.relative_to(ROOT))}
            process = subprocess.Popen(
                COMPOSE
                + [
                    "run",
                    "--rm",
                    "--name",
                    PROJECT + "-" + role,
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "inventory-workload",
                    role,
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes[role] = process
        mark("before")
        wait(
            lambda: prove_progress(case, markers["before"], processes),
            "initial independent progress",
        )
        time.sleep(5)
        mark("injected")
        target = None
        if action in (
            "primary-crash",
            "replica-crash",
            "disk-failure",
            "rebuilt-crash",
        ):
            leader = primary(native_before)
            replicas = [n for n in NODES if n != leader]
            target = leader if action == "primary-crash" else replicas[-1]
            if action == "replica-crash":
                sync = [
                    r["name"]
                    for r in native_before[leader]["replication"]
                    if r["sync_state"] == "sync"
                ]
                if len(sync) != 1:
                    raise RuntimeError("expected one actual synchronous standby")
                target = sync[0]
            if action == "rebuilt-crash":
                transition = json.loads((RESULTS / "rebuilt-member.json").read_text())
                qualified = json.loads(
                    (RESULTS / "cases" / transition["case"] / "report.json").read_text()
                )
                if (
                    not qualified.get("passed")
                    or qualified.get("action") != "disk-failure"
                ):
                    raise RuntimeError(
                        "qualify an empty-disk replacement before testing its recovered copy"
                    )
                rebuilt = transition["service"]
                target = (
                    leader
                    if leader != rebuilt
                    else next(n for n in NODES if n != rebuilt)
                )
                report["rebuilt_member"] = rebuilt
            report["target"] = target
            identity = before[target]["Id"]
            restore = [target]
            call(["docker", "kill", "--signal", "KILL", identity])
            save(case / "fenced-container.json", fenced(identity))
            mark("fenced")
            hold = int(os.environ.get("NODE_DOWN_SECONDS", "45"))
            if not 30 <= hold <= 300:
                raise ValueError("NODE_DOWN_SECONDS must be 30..300")
            deadline = time.monotonic() + hold
            while time.monotonic() < deadline:
                for role, process in processes.items():
                    if process.poll() is not None:
                        raise RuntimeError(role + " exited during fault")
                time.sleep(1)
            fenced(identity)
            survivors, degraded_native = prove_survivors(
                before, [target], native_before
            )
            save(case / "native-degraded.json", degraded_native)
            if action == "primary-crash" and primary(degraded_native) == target:
                raise RuntimeError("primary did not move while old primary was fenced")
            wait(
                lambda: prove_progress(case, markers["fenced"], processes),
                "progress while member fenced",
                timeout=60,
            )
            fenced(identity)
            if action == "rebuilt-crash":
                # Full per-copy read while a different peer is fenced proves the rebuilt member serves all data.
                report["rebuilt_degraded_proof"] = tools_degraded_verify(rebuilt, case)
                fenced(identity)
            mark("recovery")
            if action == "disk-failure":
                replace_disk(target, identity, case)
                # Update marker only after a verified empty replacement starts.
                save(
                    RESULTS / ("rebuilt-member-" + case.name + ".json"),
                    {"service": target, "case": case.name},
                )
                pointer = RESULTS / "rebuilt-member.json"
                temporary = RESULTS / (".rebuilt-" + case.name)
                save(temporary, {"service": target, "case": case.name})
                os.replace(temporary, pointer)
            else:
                call(COMPOSE + ["start", target])
        elif action in ("cold-reopen", "crash-reopen"):
            restore = list(NODES)
            if action == "cold-reopen":
                stop_signal = 'SIGINT' if MODE=='single' else 'SIGTERM'
                save(case/'shutdown-request.json',{'signal':stop_signal,'method':'Docker Compose default image signal',
                     'timeout_seconds':60,'containers':[before[n]['Id'] for n in NODES]})
                shutdown_output=call(COMPOSE+['stop','--timeout','60']+NODES,timeout=120)
                (case / "shutdown-command.log").write_text(shutdown_output)
                stopped = containers()
                save(case / "stopped-containers.json", stopped)
                if any(stopped[n]["State"]["Running"] for n in NODES):
                    raise RuntimeError("whole database did not stop")
                if any(
                    stopped[n]["State"]["ExitCode"] != 0
                    or stopped[n]["State"]["OOMKilled"]
                    for n in NODES
                ):
                    raise RuntimeError(
                        "unexpected shutdown status: "
                        + repr(
                            {
                                n: {
                                    k: stopped[n]["State"][k]
                                    for k in [
                                        "ExitCode",
                                        "OOMKilled",
                                        "StartedAt",
                                        "FinishedAt",
                                    ]
                                }
                                for n in NODES
                            }
                        )
                    )
            else:
                call(
                    ["docker", "kill", "--signal", "KILL"]
                    + [before[n]["Id"] for n in NODES]
                )
                save(
                    case / "fenced-containers.json",
                    {n: fenced(before[n]["Id"]) for n in NODES},
                )
            mark("fenced")
            time.sleep(15)
            mark("recovery")
            call(COMPOSE + ["start"] + NODES)
        else:
            time.sleep(15)
            mark("recovery")
        report["native_recovered"] = tools("healthy")
        after = containers()
        save(case / "containers-recovered.json", after)
        changed = (
            NODES
            if action in ("cold-reopen", "crash-reopen")
            else ([target] if target else [])
        )
        for member in changed:
            if (
                report["native_recovered"][member]["postmaster_started"]
                == native_before[member]["postmaster_started"]
            ):
                raise RuntimeError(
                    "target PostgreSQL process did not restart: " + member
                )
            if (
                report["native_recovered"][member]["system_identifier"]
                != native_before[member]["system_identifier"]
            ):
                raise RuntimeError(
                    "recovered member belongs to another database cluster"
                )
            if action != "disk-failure" and mount_records(
                before[member]
            ) != mount_records(after[member]):
                raise RuntimeError("retained-volume recovery changed its mounts")
        mark("native-recovered")
        wait(
            lambda: prove_progress(case, markers["native-recovered"], processes),
            "post-recovery independent progress",
        )
        time.sleep(5)
        report["writer"] = stop("writer")
        mark("writer-stopped")
        report["verification"] = tools("verify")
        wait(
            lambda: reader_progress(
                case, markers["writer-stopped"], processes["reader"]
            ),
            "reader alive through full-copy reconciliation",
        )
        report["reader"] = stop("reader")
        excluded = (
            NODES
            if action in ("cold-reopen", "crash-reopen")
            else ([target] if target else [])
        )
        report["survivors_after"] = prove_survivors(before, excluded, native_before)[1]
        report["passed"] = True
    except BaseException as err:
        report["error"] = str(err)
        try:
            save(case / "failure-state.json", tools("state"))
            (case / "database.log").write_text(
                call(
                    COMPOSE + ["logs", "--no-color", "--tail", "150"] + SERVICES,
                    timeout=60,
                )
            )
        except Exception as diagnostic:
            report["diagnostic_error"] = str(diagnostic)
        raise
    finally:
        # Heal only injected faults; do not reload tables, erase journals, or retry mutations.
        if restore:
            try:
                call(COMPOSE + ["start"] + restore, timeout=60)
            except Exception as err:
                report["cleanup_error"] = str(err)
        for role, process in processes.items():
            if process.poll() is None:
                (case / ("stop-" + role)).touch()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    call(
                        ["docker", "stop", "--time", "5", PROJECT + "-" + role],
                        timeout=30,
                    )
                    process.wait(timeout=10)
        for log in logs:
            log.close()
        ended = time.time()
        report["phases"] = markers
        report["windows"] = {}
        phases = list(markers.items())
        for i, (phase, start) in enumerate(phases):
            end = phases[i + 1][1] if i + 1 < len(phases) else ended
            report["windows"][phase] = {
                "seconds": end - start,
                "writes": window(records(case / "writer.jsonl"), start, end),
                "reads": window(records(case / "reader.jsonl"), start, end),
            }
        if report.get("cleanup_error"):
            report["passed"] = False
        report["observed_completion_gaps"] = {}
        for role, stages in [("writer", ["ack"]), ("reader", ["get", "search"])]:
            events = records(case / (role + ".jsonl"))
            for stage in stages:
                times = sorted(e["time"] for e in events if e["stage"] == stage)
                report["observed_completion_gaps"][stage] = {
                    "successful_requests": len(times),
                    "longest_gap_seconds": max(
                        (b - a for a, b in zip(times, times[1:])), default=None
                    ),
                    "first_success_after_fencing_seconds": next(
                        (
                            e["time"] - markers["fenced"]
                            for e in events
                            if e["stage"] == stage
                            and "fenced" in markers
                            and e["time"] - e.get("seconds", 0) >= markers["fenced"]
                        ),
                        None,
                    ),
                }
        report["measurement_limits"] = (
            "Phases use response completion timestamps. Gaps include offered-load think time and polling; they are not exact native election times."
        )
        save(case / "report.json", report)
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "case": report["case"],
                    "error": report.get("error"),
                }
            ),
            flush=True,
        )
        if report.get("cleanup_error"):
            raise RuntimeError("fault cleanup failed: " + report["cleanup_error"])


def reader_progress(case, since, process):
    if process.poll() is not None:
        raise RuntimeError("reader exited during verification")
    values = records(case / "reader.jsonl")
    if any(e["stage"] == "wrong" for e in values):
        raise RuntimeError("reader found wrong data")
    return all(
        sum(
            e["stage"] == kind and e["time"] - e.get("seconds", 0) >= since
            for e in values
        )
        >= 3
        for kind in ["get", "search"]
    )


def tools_degraded_verify(host, case):
    return tools("verify-rebuilt", {"VERIFY_HOST": host, "ACTIVE_CASE": case.name})


def resume_disk_recovery():
    identity = os.environ.get("DISK_CASE", "")
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("DISK_CASE must identify a recorded failed case")
    case = RESULTS / "cases" / identity
    report = json.loads((case / "report.json").read_text())
    if report.get("passed") or report.get("action") != "disk-failure":
        raise RuntimeError("resume only a recorded failed disk replacement")
    target = report["target"]
    old = json.loads((case / "fenced-container.json").read_text())
    volume = json.loads((case / "old-volume.json").read_text())
    if (
        old["State"]["Running"]
        or old["State"]["ExitCode"] != 137
        or old["State"]["OOMKilled"]
    ):
        raise RuntimeError("record does not prove old-owner fencing")
    recovery = case / ("manual-recovery-" + uuid.uuid4().hex)
    recovery.mkdir()
    provision_replacement(target, old["Id"], volume, recovery)
    native = tools("healthy")
    verification = tools("verify")
    before = json.loads((case / "containers-before.json").read_text())
    native_before = json.loads((case / "native-before.json").read_text())
    prove_survivors(before, [target], native_before)
    save(
        recovery / "recovery.json",
        {
            "recovered": True,
            "original_case_remains_failed": True,
            "scope": "administrative recovery without live actors; rerun the full fault case to qualify it",
            "native": native,
            "verification": verification,
        },
    )
    print(
        "Recovered retained witness against native replacement; failed test report unchanged",
        flush=True,
    )


def main():
    os.chdir(ROOT)
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        action = sys.argv[1]
        if action == "preflight":
            identities = call(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    "label=com.docker.compose.project=" + PROJECT,
                ]
            ).split()
            for identity in identities:
                row = inspect_id(identity)
                service = row["Config"]["Labels"].get("com.docker.compose.service")
                if (
                    MODE == "single"
                    and service in ["pg0", "pg1", "pg2", "etcd0", "etcd1", "etcd2"]
                ) or (MODE == "cluster" and service == "single"):
                    raise RuntimeError(
                        "finish the other mode lifecycle before switching topology"
                    )
        elif action == "resume-disk-recovery":
            resume_disk_recovery()
        elif action in ("up", "state"):
            print(json.dumps(tools(action)), flush=True)
        elif action == "test":
            tools("verify")
            tools("search-check")
            experiment("correctness")
        elif action == "benchmark":
            verification = tools("verify")
            before = containers()
            native_before = tools("state")
            directory = RESULTS / "benchmarks" / uuid.uuid4().hex
            directory.mkdir(parents=True)
            save(directory / "precondition.json", verification)
            save(directory / "native-before.json", native_before)
            save(directory / "containers-before.json", before)
            (directory / "runner-source.py").write_bytes(
                (ROOT / "retrieval-corpus/client/bench.py").read_bytes()
            )
            configuration = json.loads(
                call(COMPOSE + ["--profile", "tools", "config", "--format", "json"])
            )
            client = configuration["services"]["tools"]
            save(
                directory / "workload-budget.json",
                {
                    "cpus": client["cpus"],
                    "memory_bytes": client["mem_limit"],
                    "image": client["image"],
                    "trials": 1,
                    "cache": "warm; OS caches not cleared",
                    "measurement": "Docker CPU/memory samples; not integrated CPU time or peak RSS",
                },
            )
            benchmark_env = ENV | {"RESULTS_DIR": str(directory.relative_to(ROOT))}
            try:
                output = call(
                    ["python3", "retrieval-corpus/client/bench.py"],
                    env=benchmark_env,
                    timeout=900,
                )
                (directory / "command.log").write_text(output)
                resources = json.loads((directory / "resources.json").read_text())
                if not resources["samples"] or resources["sampling_errors"]:
                    raise RuntimeError(
                        "resource sampling incomplete: "
                        + repr(resources["sampling_errors"])
                    )
                after = containers()
                native_after = tools("state")
                for service in SERVICES:
                    if (
                        before[service]["Id"],
                        before[service]["State"]["StartedAt"],
                    ) != (after[service]["Id"], after[service]["State"]["StartedAt"]):
                        raise RuntimeError("server restarted during benchmark")
                for service in NODES:
                    if (
                        native_before[service]["postmaster_started"]
                        != native_after[service]["postmaster_started"]
                    ):
                        raise RuntimeError("PostgreSQL restarted during benchmark")
                save(directory / "native-after.json", native_after)
            except BaseException as error:
                save(directory / "failed.json", {"error": str(error)})
                raise
            print(
                "Benchmark and all-server resource samples:",
                directory.relative_to(ROOT),
                flush=True,
            )
        elif action == "down":
            # Workload teardown is native; infrastructure remains for docker-down.
            tools("verify")
            native = tools("healthy")
            statement = "DROP TABLE IF EXISTS lab_fixture,inventory,products"
            call(
                COMPOSE
                + [
                    "exec",
                    "-T",
                    primary(native),
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    statement,
                ]
            )
        elif action == "docker-down":
            identities = call(COMPOSE + ["ps", "--all", "--quiet"] + NODES).split()
            running = []
            for identity in identities:
                node = inspect_id(identity)
                if (
                    node["Config"]["Labels"].get("com.docker.compose.service")
                    not in NODES
                ):
                    raise RuntimeError("unexpected data-plane owner during teardown")
                if node["State"]["Running"]:
                    running.append(identity)
            if running:
                signal = "SIGINT" if MODE == "single" else "SIGTERM"
                tag = str(time.time_ns())
                save(
                    RESULTS / ("teardown-request-" + tag + ".json"),
                    {"signal": signal, "containers": running},
                )
                call(
                    ["docker", "stop", "--signal", signal, "--timeout", "60"] + running,
                    timeout=120,
                )
                stopped = {identity: inspect_id(identity) for identity in running}
                save(RESULTS / ("teardown-stopped-" + tag + ".json"), stopped)
                if any(
                    row["State"]["Running"]
                    or row["State"]["ExitCode"] != 0
                    or row["State"]["OOMKilled"]
                    for row in stopped.values()
                ):
                    raise RuntimeError(
                        "data-plane teardown did not stop cleanly; evidence preserved"
                    )
            call(
                [
                    "docker",
                    "compose",
                    "--file",
                    str(ROOT / "docker-compose.yml"),
                    "--project-name",
                    PROJECT,
                    "--profile",
                    "single",
                    "--profile",
                    "cluster",
                    "--profile",
                    "tools",
                    "down",
                    "--volumes",
                    "--remove-orphans",
                ],
                timeout=180,
            )
            for image in [
                "pgvector-product-tools:dev",
                "pgvector-products-patroni:dev",
                "pgvector-product-corpus:dev",
                "pgvector-inventory-workload:dev",
            ]:
                if (
                    image
                    in call(
                        [
                            "docker",
                            "image",
                            "ls",
                            "--format",
                            "{{.Repository}}:{{.Tag}}",
                        ]
                    ).splitlines()
                ):
                    call(["docker", "image", "rm", image], timeout=120)
            retained = [p for p in RESULTS.iterdir() if p.name != "previous-runs"]
            if retained:
                archive = RESULTS / "previous-runs" / uuid.uuid4().hex
                archive.mkdir(parents=True)
                hashes = {}
                for item in retained:
                    for path in [item] if item.is_file() else item.rglob("*"):
                        if path.is_file():
                            with path.open("rb") as stream:
                                hashes[str(path.relative_to(RESULTS))] = (
                                    hashlib.file_digest(stream, "sha256").hexdigest()
                                )
                for item in retained:
                    item.rename(archive / item.name)
                for name, digest in hashes.items():
                    with (archive / name).open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                            raise RuntimeError("archived evidence changed: " + name)
                save(
                    archive / "archive-proof.json",
                    {"files": len(hashes), "sha256": hashes},
                )
                print(
                    "Preserved",
                    len(hashes),
                    "evidence files under",
                    archive,
                    flush=True,
                )
        else:
            experiment(action)


if __name__ == "__main__":
    main()
