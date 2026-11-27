"""Garage-specific fault control; independent SDK traffic and native audits."""

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from itertools import pairwise
from pathlib import Path

from witness import attempted_objects, records
from witness_archive import archive_oracles

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_SOURCE = Path(__file__).read_bytes()
BASE = Path(os.environ.get("RESULTS_DIR", "results/recovery")).resolve()
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "garage-s3-cluster")
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
SERVICES = {*[f"garage{i}" for i in range(9)], "s3"}
TARGETS = SERVICES
USER = f"{os.getuid()}:{os.getgid()}"


def run(args, *, env=None, timeout=900, **kwargs):
    try:
        result = subprocess.run(
            args,
            check=True,
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout,
            **kwargs,
        )
    except subprocess.CalledProcessError as error:
        print(error.stdout or "", end="", flush=True)
        print(error.stderr or "", end="", file=sys.stderr, flush=True)
        raise
    if result.stderr:
        print(result.stderr, end="", flush=True)
    return result.stdout


def compose(*args, **kwargs):
    return run(COMPOSE + list(args), **kwargs)


def env_for(directory):
    return os.environ | {
        "COMPOSE_PROJECT_NAME": PROJECT,
        "RESULTS_DIR": os.path.relpath(directory, ROOT),
        "DURATION": os.environ.get("FAULT_DURATION", "900"),
    }


def save(path, value):
    with path.with_suffix(".tmp").open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path.with_suffix(".tmp"), path)


def tool(directory, *args, entrypoint=None, timeout=900):
    options = ["run", "--rm", "-T", "--user", USER]
    if entrypoint:
        options += ["--entrypoint", entrypoint]
    output = compose(*options, "tools", *args, env=env_for(directory), timeout=timeout)
    print(output, end="", flush=True)
    return output


def inventory():
    ids = compose("ps", "-aq").split()
    if not ids:
        raise RuntimeError("no owned infrastructure")
    containers = json.loads(run(["docker", "inspect", *ids]))
    found = {}
    for container in containers:
        labels = container["Config"]["Labels"]
        name = labels["com.docker.compose.service"]
        if name not in SERVICES:
            continue
        if labels["com.docker.compose.project"] != PROJECT or name in found:
            raise RuntimeError("foreign or duplicate service: " + name)
        found[name] = container
    if set(found) != SERVICES:
        raise RuntimeError("incomplete infrastructure inventory")
    return found


def identity(container):
    return {key: container[key] for key in ["Id", "RestartCount"]} | {
        "StartedAt": container["State"]["StartedAt"],
        "Pid": container["State"]["Pid"],
    }


def check_survivors(before, after, target):
    for name in SERVICES - {target}:
        if (
            identity(before[name]) != identity(after[name])
            or not after[name]["State"]["Running"]
        ):
            raise RuntimeError("survivor restarted or stopped: " + name)


def volumes(containers):
    names = sorted(
        {
            m["Name"]
            for c in containers.values()
            for m in c["Mounts"]
            if m["Type"] == "volume"
        }
    )
    values = json.loads(run(["docker", "volume", "inspect", *names]))
    if any(
        (v.get("Labels") or {}).get("com.docker.compose.project") != PROJECT
        for v in values
    ):
        raise RuntimeError("foreign volume")
    return {v["Name"]: v["CreatedAt"] for v in values}


def fixture_check(*, allow_initializing=False):
    value = json.loads((BASE / "fixture.json").read_text())
    current = inventory()
    if value.get("stage") != "ready" and not (
        allow_initializing and value.get("stage") == "initializing"
    ):
        raise RuntimeError(
            "interrupted bootstrap requires diagnosis; no seed or oracle is overwritten"
        )
    if value["project"] != PROJECT or value["volumes"] != volumes(current):
        raise RuntimeError(
            "fixture storage changed; preserve evidence and diagnose before proceeding"
        )
    if any(
        not c["State"]["Running"]
        or c["State"].get("Health", {}).get("Status") != "healthy"
        for c in current.values()
    ):
        raise RuntimeError("fixture has stopped members")
    return current


def history():
    values = []
    seen = set()
    for case in sorted(BASE.glob("fault-*")):
        if not case.is_dir():
            continue
        for value in attempted_objects(
            records(case / "churn.jsonl")
        ) + attempted_objects(records(case / "multipart.jsonl")):
            key = value["object"]["key"]
            if key in seen:
                raise RuntimeError("repeated object across histories: " + key)
            seen.add(key)
            values.append(value)
    save(BASE / "fault-history.json", values)
    return values


def api(node, endpoint, payload=None):
    return json.loads(
        compose(
            "exec",
            "-e",
            "RUST_LOG=warn",
            "-T",
            node,
            "/garage",
            "json-api",
            endpoint,
            json.dumps(payload),
            timeout=30,
        )
    )


def healthy(directory, processes=None):
    deadline = time.monotonic() + 120
    while True:
        if processes:
            live(processes)
        values = {f"garage{i}": api(f"garage{i}", "GetClusterHealth") for i in range(9)}
        save(directory / "native-health.json", values)
        if all(
            v["status"] == "healthy"
            and v["storageNodes"] == 9
            and v["storageNodesUp"] == 9
            and v["partitionsAllOk"] == 256
            and v["partitionsQuorum"] == 256
            for v in values.values()
        ):
            return
        if time.monotonic() > deadline:
            raise RuntimeError("native membership/quorum did not recover")
        time.sleep(1)


def metadata_idle(stats, workers, include_block_errors=True):
    tables = stats["tableStats"]
    if not tables:
        raise RuntimeError("native table statistics unavailable")
    indexed = {w["name"]: w for w in workers}
    required = {
        t["tableName"] + suffix
        for t in tables
        for suffix in [" sync", " Merkle", " queue"]
    }
    if not required <= set(indexed):
        raise RuntimeError("native table-sync/index worker missing")
    # Delayed GC is separate from live-body repair. Zero queued items alone is
    # insufficient: a worker can already be applying a popped batch.
    return (
        (not include_block_errors or stats["blockManagerStats"]["resyncErrors"] == 0)
        and all(t["merkleQueueLen"] == 0 and t["insertQueueLen"] == 0 for t in tables)
        and all(
            indexed[name]["state"] == "idle" and indexed[name]["consecutiveErrors"] == 0
            for name in required
        )
    )


def queues(directory, processes=None, include_block_errors=True):
    deadline = time.monotonic() + 1200
    peers = {
        p.split("@")[0]
        for p in json.loads((BASE / "fixture.json").read_text())["peers"]
    }
    while True:
        if processes:
            live(processes)
        value = api("garage0", "GetNodeStatistics", {"node": "*", "body": None})
        save(directory / "native-statistics.json", value)
        if value["error"] or set(value["success"]) != peers:
            raise RuntimeError("missing/failed native node statistics")
        workers = api("garage0", "ListWorkers", {"node": "*", "body": {}})
        save(directory / "native-workers.json", workers)
        if workers["error"] or set(workers["success"]) != peers:
            raise RuntimeError("missing/failed native worker inventory")
        quiet = all(
            metadata_idle(
                value["success"][peer], workers["success"][peer], include_block_errors
            )
            for peer in peers
        )
        if quiet:
            return
        if time.monotonic() > deadline:
            raise RuntimeError("native metadata queues/resync errors did not clear")
        time.sleep(2)


def refresh_metadata(directory, processes, number):
    live(processes)
    workers = api("garage0", "ListWorkers", {"node": "*", "body": {}})
    peers = {
        p.split("@")[0]
        for p in json.loads((BASE / "fixture.json").read_text())["peers"]
    }
    if workers["error"] or set(workers["success"]) != peers:
        raise RuntimeError("native repair worker inventory unavailable")
    stats = api("garage0", "GetNodeStatistics", {"node": "*", "body": None})
    if stats["error"] or set(stats["success"]) != peers:
        raise RuntimeError("native metadata statistics unavailable")
    if not all(
        metadata_idle(
            stats["success"][peer], workers["success"][peer], include_block_errors=False
        )
        for peer in peers
    ):
        return False
    started = time.time()
    logs = []
    for i in range(9):
        live(processes)
        logs.append(
            compose(
                "exec",
                "-e",
                "RUST_LOG=warn",
                "-T",
                f"garage{i}",
                "/garage",
                "repair",
                "--yes",
                "tables",
                timeout=30,
            )
        )
    (directory / f"metadata-sweep-{number}.log").write_text("\n".join(logs))
    with (directory / "metadata-sweeps.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "sweep": number,
                    "started_at": started,
                    "finished_at": time.time(),
                    "live_roles": sorted(processes),
                }
            )
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    print("NATIVE METADATA SWEEP", number, flush=True)
    return True


def native(directory, mode, processes=None, refresh_pending=False):
    name = PROJECT + "-audit-" + uuid.uuid4().hex[:12]
    command = COMPOSE + [
        "run",
        "--rm",
        "-T",
        "--name",
        name,
        "--user",
        USER,
        "audit",
        mode,
    ]
    log_path = directory / f"native-{mode}-{uuid.uuid4().hex[:8]}.log"
    with log_path.open("w") as log:
        job = subprocess.Popen(
            command, env=env_for(directory), stdout=log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 1300
        next_refresh = time.monotonic() + 30
        sweeps = 0
        try:
            while job.poll() is None:
                if processes:
                    live(processes)
                if refresh_pending and time.monotonic() >= next_refresh:
                    if sweeps >= 8:
                        raise RuntimeError(
                            "eight metadata recovery sweeps did not restore the live ACK prefix"
                        )
                    if refresh_metadata(directory, processes or {}, sweeps + 1):
                        sweeps += 1
                    next_refresh = time.monotonic() + 30
                if time.monotonic() > deadline:
                    raise RuntimeError("native physical audit deadline exceeded")
                time.sleep(0.25)
            if job.returncode:
                print(log_path.read_text(), flush=True)
                raise RuntimeError("native physical audit failed")
        finally:
            if job.poll() is None:
                run(["docker", "stop", "--time", "5", name], timeout=30)
                job.wait(timeout=30)
    print(log_path.read_text(), end="", flush=True)


def prepare_disk(directory, target):
    mount = next(
        m
        for m in target["Mounts"]
        if m["Type"] == "volume" and m["Destination"] == "/meta"
    )
    info = json.loads(run(["docker", "volume", "inspect", mount["Name"]]))[0]
    if info["Labels"]["com.docker.compose.project"] != PROJECT:
        raise RuntimeError("refusing foreign metadata disk")
    save(directory / "retained-meta.json", info)
    # Native documented marker recovery; keep the metadata DB and node identity.
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            mount["Name"] + ":/meta",
            "--entrypoint",
            "python",
            PROJECT + "-tools:dev",
            "-c",
            "from pathlib import Path; Path('/meta/data_layout').unlink()",
        ],
        timeout=30,
    )


def repair(directory, processes=None):
    # Container readiness is not native peer convergence. A full-sync launch
    # must see the repaired member, then metadata must precede block repair.
    healthy(directory, processes)
    queues(directory, processes, include_block_errors=False)
    outputs = []
    started = time.time()
    repair_id = uuid.uuid4().hex[:12]
    for i in range(9):
        if processes:
            live(processes)
        outputs.append(
            compose(
                "exec",
                "-e",
                "RUST_LOG=warn",
                "-T",
                f"garage{i}",
                "/garage",
                "repair",
                "--yes",
                "tables",
                timeout=30,
            )
        )
    queues(directory, processes, include_block_errors=False)
    for i in range(9):
        if processes:
            live(processes)
        outputs.append(
            compose(
                "exec",
                "-e",
                "RUST_LOG=warn",
                "-T",
                f"garage{i}",
                "/garage",
                "repair",
                "--yes",
                "blocks",
                timeout=30,
            )
        )
    (directory / f"repair-{repair_id}.log").write_text("\n".join(outputs))
    with (directory / "repairs.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "id": repair_id,
                    "started_at": started,
                    "finished_at": time.time(),
                    "live_roles": sorted(processes or {}),
                }
            )
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def live_prefix(directory, processes):
    live(processes)
    prefix = directory / "live-repair-prefix"
    prefix.mkdir()
    for name in [
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "fixture",
        "multipart-published",
    ]:
        path = directory / (name + ".json")
        if path.exists():
            shutil.copy2(path, prefix / path.name)
    data = (directory / "churn.jsonl").read_bytes()
    data = data[: data.rfind(b"\n") + 1]
    values = attempted_objects([json.loads(line) for line in data.splitlines()])
    if sum(v["acked"] for v in values) < 5:
        raise RuntimeError("no live ACK prefix to repair")
    with (prefix / "churn.jsonl").open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    save(
        prefix / "prefix.json",
        {"ack_objects": sum(v["acked"] for v in values), "captured_at": time.time()},
    )
    native(prefix, "snapshot", processes)
    live(processes)
    return prefix


def verify(directory, processes=None, *, refresh_pending=False):
    healthy(directory, processes)
    if (directory / "churn.jsonl").exists():
        tool(directory, "verify")
    else:
        tool(
            directory,
            "-c",
            'import workload as w; w.save("reconciliation",w.Store().verify())',
            entrypoint="python",
        )
    healthy(directory, processes)
    native(directory, "snapshot", processes)
    native(directory, "wait", processes, refresh_pending=refresh_pending)
    queues(directory, processes)


def diagnostics(directory, containers, label):
    out = directory / ("server-logs-" + label)
    out.mkdir(exist_ok=True)
    for name, value in containers.items():
        with (out / (name + ".log")).open("w") as stream:
            subprocess.run(
                ["docker", "logs", "--timestamps", "--tail", "5000", value["Id"]],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=30,
            )


def phase(directory, name):
    path = directory / "phases.jsonl"
    with path.open("a") as stream:
        stream.write(json.dumps({"phase": name, "time": time.time()}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print("PHASE", name, flush=True)


def live(processes):
    for name, process in processes.items():
        if process.poll() is not None:
            raise RuntimeError(name + " exited before native recovery")


def progress(directory, processes, since, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        live(processes)
        counts = []
        for file, stage in [("churn.jsonl", "ack"), ("reader.jsonl", "get")]:
            path = directory / file
            data = path.read_bytes() if path.exists() else b""
            lines = data.split(b"\n")[:-1]
            counts.append(
                sum(
                    e["stage"] == stage and e["time"] >= since
                    for e in map(json.loads, lines)
                )
            )
        if min(counts) >= 5:
            return
        time.sleep(0.25)
    raise RuntimeError("no live read/write progress within bounded wait")


def distributions(events):
    result = {"count": len(events)}
    if not events:
        return result
    values = sorted(e["seconds"] * 1000 for e in events)
    result |= {
        f"p{p}_ms": values[math.ceil(len(values) * p / 100) - 1] for p in [50, 99]
    }
    result["max_ms"] = values[-1]
    return result


def report(directory):
    marks = records(directory / "phases.jsonl")
    reads = records(directory / "reader.jsonl")
    writes = [r for r in records(directory / "churn.jsonl") if r["stage"] != "attempt"]
    sections = {}
    for begin, end in pairwise(marks):
        duration = end["time"] - begin["time"]
        section = {"seconds": duration}
        for stage, events in [
            ("ack", writes),
            ("ambiguous", writes),
            ("get", reads),
            ("get_error", reads),
        ]:
            subset = [
                e
                for e in events
                if e["stage"] == stage and begin["time"] <= e["time"] < end["time"]
            ]
            section[stage] = distributions(subset) | {
                "ops_per_second": len(subset) / duration
            }
        sections[begin["phase"]] = section
    success_times = [marks[0]["time"]] + [
        r["time"] for r in reads if r["stage"] == "get"
    ]
    history_values = history()
    return {
        "phases": sections,
        "writer": {
            s: sum(r["stage"] == s for r in writes) for s in ["ack", "ambiguous"]
        },
        "reader": {
            s: sum(r["stage"] == s for r in reads)
            for s in ["get", "get_error", "wrong"]
        },
        "longest_read_completion_gap_seconds": max(
            b - a for a, b in pairwise(success_times)
        ),
        "cumulative_ack_objects": sum(v["acked"] for v in history_values),
        "histories": len([p for p in BASE.glob("fault-*") if p.is_dir()]),
    }


def data_target(copies, expected):
    owners = sorted({node for block in expected for node in copies.get(block, [])})
    if not owners or any(node not in range(9) for node in owners):
        raise RuntimeError("no known required-block owner")
    return f"garage{owners[0]}"


def fault(mode):
    before = fixture_check()
    history()
    native(BASE, "snapshot")
    native(BASE, "wait")
    copies = json.loads((BASE / "block-placement.json").read_text())
    expected = set(json.loads((BASE / "physical-baseline.json").read_text()))
    target_name = os.environ.get("FAULT_TARGET") or data_target(copies, expected)
    if target_name not in TARGETS or mode == "disk" and target_name == "s3":
        raise ValueError("target/mode is not allowlisted")
    case = (
        BASE
        / f"fault-{mode}-{target_name}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    case.mkdir()
    save(
        case / "case.json",
        {
            "id": uuid.uuid4().hex,
            "mode": mode,
            "target": target_name,
            "controller_sha256": hashlib.sha256(CONTROLLER_SOURCE).hexdigest(),
            "tools_image": json.loads(
                run(["docker", "image", "inspect", PROJECT + "-tools:dev"])
            )[0]["Id"],
        },
    )
    (case / "controller.py").write_bytes(CONTROLLER_SOURCE)
    save(case / "before.json", before)
    free_bytes = json.loads((BASE / "physical-recovery.json").read_text())[
        "docker_data_free_bytes"
    ]
    if free_bytes < 6 * 1024**3:
        raise RuntimeError("less than 6 GiB free in Docker; refusing fault injection")
    save(case / "capacity.json", {"free_bytes": free_bytes})
    for name in [
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "physical-baseline",
        "fixture",
    ]:
        p = BASE / (name + ".json")
        if p.exists():
            shutil.copy2(p, case / p.name)
    processes, names = {}, {}
    injected = False
    success = False
    disk_removed = False
    with ExitStack() as stack:
        try:
            for role in ["writer", "reader"]:
                name = f"{PROJECT}-{role}-{uuid.uuid4().hex[:12]}"
                names[role] = name
                log = stack.enter_context((case / (role + ".log")).open("w"))
                processes[role] = subprocess.Popen(
                    COMPOSE
                    + [
                        "run",
                        "--rm",
                        "-T",
                        "--name",
                        name,
                        "--user",
                        USER,
                        "--entrypoint",
                        "python",
                        "tools",
                        "/app/traffic.py",
                        role,
                    ],
                    env=env_for(case),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            phase(case, "baseline")
            progress(case, processes, 0)
            time.sleep(5)
            live(processes)
            if target_name.startswith("garage"):
                node = int(target_name.removeprefix("garage"))
                affected = [
                    block for block in expected if node in copies.get(block, [])
                ]
                if not affected:
                    raise RuntimeError("target owns no required native block")
                save(case / "affected-blocks.json", affected)
            phase(case, "injection")
            injected = True
            compose("kill", "-s", "SIGKILL", target_name)
            stopped = json.loads(run(["docker", "inspect", before[target_name]["Id"]]))[
                0
            ]
            if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 137:
                raise RuntimeError(
                    "SIGKILL did not stop the exact target with exit 137"
                )
            save(case / "stopped.json", stopped)
            diagnostics(case, {target_name: stopped}, "stopped-target")
            if mode == "disk":
                volume = next(
                    m["Name"]
                    for m in stopped["Mounts"]
                    if m["Type"] == "volume" and m["Destination"] == "/data"
                )
                original = json.loads(run(["docker", "volume", "inspect", volume]))[0]
                if original["Labels"]["com.docker.compose.project"] != PROJECT:
                    raise RuntimeError("refusing foreign disk")
                compose("rm", "-f", target_name)
                # Metadata may be absent after rm, so use a healthy runtime listing
                # to prove fencing; inspection failure is never interpreted as exit.
                remaining = run(["docker", "ps", "-aq", "--no-trunc"]).split()
                if stopped["Id"] in remaining:
                    raise RuntimeError("old disk owner still registered")
                run(["docker", "volume", "rm", volume])
                disk_removed = True
                prepare_disk(case, stopped)
                save(case / "removed-disk.json", original)
            outage_at = time.time()
            phase(case, "outage")
            time.sleep(30)
            live(processes)
            if target_name.startswith("garage"):
                # RF3/quorum2 with one native node down must still complete
                # new writes and independent reads, not merely keep jobs alive.
                progress(case, processes, outage_at, seconds=60)
            phase(case, "repair")
            started = time.monotonic()
            compose(
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "120",
                target_name,
                timeout=180,
            )
            ready_seconds = time.monotonic() - started
            healthy(case, processes)
            prefix = live_prefix(case, processes)
            repair(case, processes)
            native(case, "wait", processes)
            shutil.copy2(
                case / "physical-recovery.json", case / "preexisting-recovery.json"
            )
            native(prefix, "wait", processes, refresh_pending=True)
            repaired_at = time.time()
            phase(case, "after")
            progress(case, processes, repaired_at)
            check_survivors(before, inventory(), target_name)
            (case / "stop-churn").touch()
            if processes["writer"].wait(timeout=30):
                raise RuntimeError("writer failed")
            live({"reader": processes["reader"]})
            # The fixed live prefix already has every native copy. Include the
            # subsequent steady-state tail in a separate, quiescent final audit.
            phase(case, "reconcile")
            repair(case, {"reader": processes["reader"]})
            verify(case, {"reader": processes["reader"]})
            live({"reader": processes["reader"]})
            phase(case, "verified")
            (case / "stop-reader").touch()
            if processes["reader"].wait(timeout=30):
                raise RuntimeError("reader failed")
            after = inventory()
            check_survivors(before, after, target_name)
            save(case / "after.json", after)
            diagnostics(case, after, "final")
            if mode == "disk":
                new_volumes = volumes(after)
                if new_volumes[volume] == original["CreatedAt"]:
                    raise RuntimeError("disk was not recreated")
                witness = json.loads((BASE / "fixture.json").read_text())
                witness["volumes"] = new_volumes
                save(BASE / "fixture.json", witness)
            result = report(case) | {
                "mode": mode,
                "target": target_name,
                "ready_seconds": ready_seconds,
                "survivors_unchanged": len(SERVICES) - 1,
                "live_prefix": json.loads(
                    (case / "live-repair-prefix/prefix.json").read_text()
                ),
                "live_prefix_recovery": json.loads(
                    (case / "live-repair-prefix/physical-recovery.json").read_text()
                ),
            }
            save(case / "result.json", result)
            print(json.dumps(result, indent=2), flush=True)
            success = True
        finally:
            cleanup_errors = []
            for role, process in processes.items():
                if process.poll() is None:
                    (
                        case / ("stop-churn" if role == "writer" else "stop-reader")
                    ).touch()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        try:
                            run(
                                ["docker", "stop", "--time", "5", names[role]],
                                timeout=30,
                            )
                            process.wait(timeout=20)
                        except (subprocess.SubprocessError, OSError) as error:
                            cleanup_errors.append(str(error))
            if injected and not success:
                try:
                    compose(
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        target_name,
                        timeout=180,
                    )
                    repair(case)
                    native(case, "wait")
                except (subprocess.SubprocessError, OSError, RuntimeError) as error:
                    cleanup_errors.append(str(error))
            # Recover every fsynced attempt, including failed controller cases.
            history()
            save(
                case / "status.json",
                {
                    "passed": success,
                    "disk_removed": disk_removed,
                    "cleanup_errors": cleanup_errors,
                },
            )
            if cleanup_errors:
                raise RuntimeError("cleanup failed: " + "; ".join(cleanup_errors))


def teardown():
    fixture_path = BASE / "fixture.json"
    fixture = json.loads(fixture_path.read_text()) if fixture_path.exists() else None
    if fixture and fixture["project"] != PROJECT:
        raise RuntimeError("teardown witness belongs to another project")
    compose("--profile", "tools", "down", "--volumes", "--remove-orphans")
    tag = PROJECT + "-tools:dev"
    if run(["docker", "image", "ls", "-q", tag]).strip():
        run(["docker", "image", "rm", tag])
    label = "label=com.docker.compose.project=" + PROJECT
    remaining = {
        "containers": run(["docker", "ps", "-aq", "--filter", label]).split(),
        "volumes": run(["docker", "volume", "ls", "-q", "--filter", label]).split(),
        "networks": run(["docker", "network", "ls", "-q", "--filter", label]).split(),
        "build_tag": run(["docker", "image", "ls", "-q", tag]).split(),
    }
    if any(remaining.values()):
        raise RuntimeError("teardown left owned resources: " + str(remaining))
    save(
        BASE / "teardown.json",
        {
            "fixture_id": fixture["id"] if fixture else None,
            "project": PROJECT,
            "remaining": remaining,
            "finished": True,
        },
    )


def retire_removed_fixture():
    marker = BASE / "teardown.json"
    if not marker.exists():
        return
    value = json.loads(marker.read_text())
    fixture_path = BASE / "fixture.json"
    fixture = json.loads(fixture_path.read_text()) if fixture_path.exists() else None
    expected_id = fixture["id"] if fixture else None
    if (
        value["fixture_id"] != expected_id
        or value["project"] != PROJECT
        or value.get("finished") is not True
        or set(value["remaining"]) != {"containers", "volumes", "networks", "build_tag"}
        or any(value["remaining"].values())
    ):
        raise RuntimeError("invalid teardown proof; preserving the old witness")
    archive_oracles(
        BASE,
        extra={
            "fixture",
            "bootstrap",
            "teardown",
            "block-placement",
            "native-ring",
            "native-health",
            "native-statistics",
            "native-workers",
            "physical-failure",
            "reconciliation",
        },
    )


def finish_bootstrap():
    # Seed ACKs require all configured copies, not merely the write quorum.
    # A diagnosed interrupted startup can resume this barrier without PUT replay.
    fixture_check(allow_initializing=True)
    if not (BASE / "seed.json").exists() or not (BASE / "manifest.json").exists():
        raise RuntimeError("incomplete seed requires diagnosis; refusing to reseed")
    repair(BASE)
    verify(BASE, refresh_pending=True)
    witness = json.loads((BASE / "fixture.json").read_text())
    witness["stage"] = "ready"
    save(BASE / "fixture.json", witness)


def main():
    os.chdir(ROOT)
    BASE.mkdir(parents=True, exist_ok=True)
    action = sys.argv[1]
    if action == "docker-down":
        teardown()
        return
    if action == "up":
        retire_removed_fixture()
        if (BASE / "fixture.json").exists():
            fixture_check()
            history()
            verify(BASE)
            return
        if (BASE / "manifest.json").exists():
            raise RuntimeError(
                "unbound existing oracle; select a new RESULTS_DIR or diagnose the old fixture"
            )
        before = inventory()
        storage = volumes(before)
        peers = json.loads(Path("config/generated/bootstrap.json").read_text())["peers"]
        layout = api("garage0", "GetClusterLayout")
        roles = {v["id"]: v for v in layout["roles"]}
        if len(roles) != 9 or layout["version"] != 1:
            raise RuntimeError("expected native nine-node layout version one")
        for i, peer in enumerate(peers):
            role = roles[peer.split("@")[0]]
            if role["zone"] != f"zone{i // 3}" or role["capacity"] != 4000000000:
                raise RuntimeError("unexpected native zone/capacity")
        witness = {
            "id": str(uuid.uuid4()),
            "project": PROJECT,
            "volumes": storage,
            "peers": peers,
            "native_layout": layout,
            "stage": "initializing",
        }
        save(BASE / "bootstrap.json", {"containers": before, "witness": witness})
        save(BASE / "fixture.json", witness)
        tool(BASE, "create")
        tool(BASE, "seed")
        finish_bootstrap()
        return
    if action == "recover-bootstrap":
        witness = json.loads((BASE / "fixture.json").read_text())
        if witness.get("stage") != "initializing":
            raise RuntimeError("only an interrupted bootstrap may use this action")
        finish_bootstrap()
    elif action == "verify":
        fixture_check()
        history()
        verify(BASE)
    elif action == "repair":
        fixture_check()
        history()
        repair(BASE)
        verify(BASE)
    elif action in ["process", "disk"]:
        fault(action)
    else:
        raise ValueError("unknown action")


if __name__ == "__main__":
    main()
