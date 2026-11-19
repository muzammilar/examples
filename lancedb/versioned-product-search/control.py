"""Serial process and catalog-maintenance controller for the embedded LanceDB lab."""

import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
PROJECT = "lancedb-versioned-product-search"
MODE = os.environ.get("MODE", "cluster")
if MODE not in ("single", "cluster"):
    raise ValueError("MODE must be single or cluster")
NODES = ["writer"] + ([] if MODE == "single" else ["reader1", "reader2", "reader3"])
SERVICES = NODES
RESULTS = (ROOT / os.environ.get("RESULTS_DIR", "results/production/" + MODE)).resolve()
if not RESULTS.is_relative_to(ROOT / "results"):
    raise ValueError("results must be example-owned")
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
    "RESULTS_DIR": str(RESULTS.relative_to(ROOT)),
    "URLS": ",".join("http://" + n + ":8080" for n in NODES if n != "writer")
    if MODE == "cluster"
    else "http://writer:8080",
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


def tools(action):
    path = command_result(action)
    output = call(
        COMPOSE
        + [
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "python",
            "tools",
            "/app/production.py",
            action,
        ],
        timeout=900,
    )
    if not path.exists():
        raise RuntimeError("native command wrote no fresh result: " + output[-3000:])
    result = json.loads(path.read_text())
    if action in ("healthy", "verify", "up"):
        expected = hashlib.sha256((ROOT / "app/production.py").read_bytes()).hexdigest()
        if result.get("verifier_source_sha256") != expected:
            raise RuntimeError("stale native verifier image; rebuild")
    if result.get("scope") == "views-of-one-shared-catalog":
        from view_proofs import audit_views

        documents = json.loads(
            (ROOT / "results/corpus/prepared/manifest.json").read_text()
        )["parameters"]["documents"]
        proof = audit_views(result, RESULTS, NODES, documents)
        save(RESULTS / ("view-proof-audit-" + str(time.time_ns()) + ".json"), proof)
        result["view_proof_audit"] = proof
    return result


def assert_adapter_sources(nodes):
    expected = hashlib.sha256((ROOT / "app/server.py").read_bytes()).hexdigest()
    for node, row in nodes.items():
        observed = call(
            ["docker", "exec", row["Id"], "sha256sum", "/app/server.py"], timeout=30
        ).split()
        if not observed or observed[0] != expected:
            raise RuntimeError(
                "running SDK wrapper differs from this checkout; rerun make docker-up: "
                + node
            )


def prove_survivors(before, excluded):
    current = containers()
    for node in set(NODES) - set(excluded):
        old, new = before[node], current[node]
        if (old["Id"], old["State"]["StartedAt"]) != (
            new["Id"],
            new["State"]["StartedAt"],
        ) or not new["State"]["Running"]:
            raise RuntimeError("unrelated SDK process restarted: " + node)
    return current


def native_layout():
    # Public statistics from a pinned SDK snapshot; no mutation or second writer.
    program = (
        "import lancedb,json,dataclasses; "
        'db=lancedb.connect("/data/catalog"); t=db.open_table("products"); '
        "version=t.version; t.checkout(version); "
        'print(json.dumps({"catalog_version":version,"stats":t.stats(),'
        '"indexes":{i.name:dataclasses.asdict(t.index_stats(i.name)) for i in t.list_indices()}})); '
        "db.close()"
    )
    result = json.loads(
        call(COMPOSE + ["exec", "-T", "writer", "python", "-c", program], timeout=120)
    )
    if result["stats"]["num_rows"] != int(os.environ.get("DOCUMENTS", "100000")):
        raise RuntimeError("native fragment inventory has wrong row count")
    return result


def hold_fault(case, since, processes, targets):
    hold = int(os.environ.get("NODE_DOWN_SECONDS", "45"))
    if not 30 <= hold <= 300:
        raise ValueError("NODE_DOWN_SECONDS must be 30..300")
    deadline = time.monotonic() + hold
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes.values()):
            raise RuntimeError("actor exited while fault held")
        time.sleep(1)
    for identity in targets:
        fenced(identity)
    if MODE == "cluster":
        # Reader availability is observed independently of a failed writer.
        if not reader_progress(case, since, processes["reader"]):
            raise RuntimeError("no independent reads while peer fenced")


def experiment(action):
    if action not in {
        "correctness",
        "writer-crash",
        "reader-crash",
        "maintenance",
        "rolling-restart",
        "cold-reopen",
        "crash-reopen",
    }:
        raise ValueError("unknown case")
    if action == "reader-crash" and MODE != "cluster":
        raise RuntimeError("reader crash requires cluster mode")
    RESULTS.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < 4 * 1024**3:
        raise RuntimeError(
            "fewer than 4 GiB free; preserve evidence and make room before further faults"
        )
    before = containers()
    assert_adapter_sources(before)
    native_before = tools("healthy")
    if action == "cold-reopen" and any(
        before[n]["Config"].get("StopSignal") != "SIGTERM" for n in NODES
    ):
        raise RuntimeError("wrong SDK image default shutdown signal")
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
        changed = []
        if action in ("writer-crash", "reader-crash"):
            target = "writer" if action == "writer-crash" else "reader1"
            changed = [target]
            restore = [target]
            identity = before[target]["Id"]
            call(["docker", "kill", "--signal", "KILL", identity])
            save(case / "fenced-container.json", fenced(identity))
            mark("fenced")
            hold_fault(case, markers["fenced"], processes, [identity])
            prove_survivors(before, changed)
            degraded = tools("state")
            save(case / "native-degraded.json", degraded)
            if "error" not in degraded[target] or any(
                "error" in degraded[n] for n in NODES if n != target
            ):
                raise RuntimeError(
                    "SDK status does not demonstrate the isolated role outage"
                )
            if action == "reader-crash":
                observed = records(case / "reader.jsonl")
                if not any(
                    e["stage"] in ("get_error", "search_error")
                    and e.get("server") == "http://reader1:8080"
                    and e["time"] - e.get("seconds", 0) >= markers["fenced"]
                    for e in observed
                ):
                    raise RuntimeError(
                        "crashed query worker produced no observed read failure"
                    )
                wait(
                    lambda: prove_progress(case, markers["fenced"], processes),
                    "surviving read/write progress",
                )
            else:
                events = records(case / "writer.jsonl")
                if not any(
                    e["stage"] == "ambiguous"
                    and e["time"] - e.get("seconds", 0) >= markers["fenced"]
                    for e in events
                ):
                    raise RuntimeError("writer outage was not observed")
                if any(
                    e["stage"] == "ack"
                    and e["time"] - e.get("seconds", 0) >= markers["fenced"]
                    for e in events
                ):
                    raise RuntimeError(
                        "writes acknowledged after sole writer was fenced"
                    )
            mark("recovery")
            call(COMPOSE + ["start", target])
        elif action == "maintenance":
            report["layout_before"] = native_layout()
            started = time.monotonic()
            report["maintenance"] = tools("maintenance")
            report["maintenance_wall_seconds"] = time.monotonic() - started
            report["layout_after"] = native_layout()
            before_fragments = report["layout_before"]["stats"]["fragment_stats"][
                "num_fragments"
            ]
            after_fragments = report["layout_after"]["stats"]["fragment_stats"][
                "num_fragments"
            ]
            if after_fragments >= before_fragments:
                raise RuntimeError(
                    "native maintenance did not prove small-fragment compaction"
                )
            mark("recovery")
        elif action == "rolling-restart":
            restore = list(NODES)
            changed = list(NODES)
            for index, member in enumerate(NODES):
                call(COMPOSE + ["stop", "--timeout", "60", member], timeout=90)
                stopped = inspect_id(before[member]["Id"])
                save(case / f"rolling-{member}-stopped.json", stopped)
                if (
                    stopped["State"]["Running"]
                    or stopped["State"]["ExitCode"] != 0
                    or stopped["State"]["OOMKilled"]
                ):
                    raise RuntimeError("rolling stop was not clean")
                call(COMPOSE + ["start", member])
                tools("healthy")
                prove_survivors(before, changed[: index + 1])
                mark("rolled-" + member)
                wait(
                    lambda: prove_progress(
                        case, markers["rolled-" + member], processes
                    ),
                    "rolling post-reopen progress",
                )
            mark("recovery")
        elif action in ("cold-reopen", "crash-reopen"):
            restore = list(NODES)
            changed = list(NODES)
            if action == "cold-reopen":
                call(COMPOSE + ["stop", "--timeout", "60"] + NODES, timeout=120)
                stopped = containers()
                save(case / "stopped-containers.json", stopped)
                if any(
                    s["State"]["Running"]
                    or s["State"]["ExitCode"] != 0
                    or s["State"]["OOMKilled"]
                    for s in stopped.values()
                ):
                    raise RuntimeError("whole-stack SDK shutdown was not clean")
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
        report["changed"] = changed
        report["native_recovered"] = tools("healthy")
        after = containers()
        save(case / "containers-recovered.json", after)
        for member in changed:
            if (
                after[member]["State"]["StartedAt"]
                == before[member]["State"]["StartedAt"]
            ):
                raise RuntimeError("target SDK process did not restart")
            if mount_records(before[member]) != mount_records(after[member]):
                raise RuntimeError("retained shared catalog mount changed")
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
        report["survivors_after"] = prove_survivors(before, changed)
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
            if MODE == "single":
                for identity in identities:
                    row = inspect_id(identity)
                    if row["Config"]["Labels"].get("com.docker.compose.service") in [
                        "reader1",
                        "reader2",
                        "reader3",
                    ]:
                        raise RuntimeError(
                            "finish the cluster lifecycle before switching to single mode"
                        )
        elif action in ("up", "state"):
            if action == "up":
                assert_adapter_sources(containers())
            print(json.dumps(tools(action)), flush=True)
        elif action == "test":
            assert_adapter_sources(containers())
            tools("verify")
            tools("search-check")
            experiment("correctness")
        elif action == "benchmark":
            verification = tools("verify")
            before = containers()
            assert_adapter_sources(before)
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
            assert_adapter_sources(containers())
            tools("verify")
            tools("drop")
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
                signal = "SIGTERM"
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
                "lancedb-product-tools:dev",
                "lancedb-product-corpus:dev",
                "lancedb-inventory-workload:dev",
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
