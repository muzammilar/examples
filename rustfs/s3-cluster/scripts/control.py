"""RustFS-native live failure control with retained external witnesses."""

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

from native_health import member_identity, replacement_complete
from witness import attempted_objects, records
from witness_archive import archive_oracles

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_SOURCE = Path(__file__).read_bytes()
BASE = (ROOT / os.environ.get("RESULTS_DIR", "results/recovery")).resolve()
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "rustfs-s3-cluster")
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
SERVICES = {*[f"rustfs{i}" for i in range(4)], "s3"}
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
    validate_mounts(found)
    return found


def validate_mounts(containers):
    for node in SERVICES - {"s3"}:
        mounts = {
            m["Destination"]: m
            for m in containers[node]["Mounts"]
            if m["Type"] == "volume"
        }
        parent = mounts.get("/data")
        drive = mounts.get("/data/disk0")
        if not parent or not drive or parent["Name"] != drive["Name"]:
            raise RuntimeError(
                "native drive must be a direct mount of the owned disk0 subdirectory: "
                + node
            )


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


def history():
    values = []
    seen = set()
    for case in sorted(BASE.glob("fault-*")):
        if not case.is_dir():
            continue
        for value in attempted_objects(records(case / "churn.jsonl")):
            key = value["object"]["key"]
            if key in seen:
                raise RuntimeError("repeated object across histories: " + key)
            seen.add(key)
            values.append(value)
    save(BASE / "fault-history.json", values)
    return values


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
            "native-membership",
            "native-health",
            "native-formats",
            "physical-failure",
            "prefix-readback",
            "reconciliation",
        },
    )


def run_job(directory, service, script, command, processes=None):
    name = PROJECT + "-check-" + uuid.uuid4().hex[:12]
    log_path = directory / f"{command}-{uuid.uuid4().hex[:10]}.log"
    stderr_path = log_path.with_suffix(".stderr.log")
    with log_path.open("x") as log, stderr_path.open("x") as errors:
        job = subprocess.Popen(
            COMPOSE
            + [
                "run",
                "--rm",
                "-T",
                "--name",
                name,
                "--user",
                "0:0" if service == "audit" else USER,
                "--entrypoint",
                "python",
                service,
                script,
                command,
            ],
            env=env_for(directory),
            stdout=log,
            stderr=errors if service == "audit" else subprocess.STDOUT,
        )
        deadline = time.monotonic() + 1300
        try:
            while job.poll() is None:
                if processes:
                    live(processes)
                if time.monotonic() > deadline:
                    raise RuntimeError("native job deadline exceeded")
                time.sleep(0.25)
            if service == "audit":
                output = log_path.read_text()
                if output:
                    result = json.loads(output)
                    allowed = {
                        "native-formats",
                        "physical-baseline",
                        "physical-recovery",
                        "physical-failure",
                    }
                    if not set(result) <= allowed:
                        raise RuntimeError("unknown read-only audit result")
                    expected = {
                        "native-formats",
                        "physical-baseline"
                        if command == "snapshot"
                        else "physical-recovery",
                    }
                    if job.returncode == 0 and set(result) != expected:
                        raise RuntimeError("read-only audit omitted required evidence")
                    for kind, value in result.items():
                        save(directory / (kind + ".json"), value)
                elif job.returncode == 0:
                    raise RuntimeError("read-only auditor omitted its result")
            if job.returncode:
                print(log_path.read_text(), flush=True)
                print(stderr_path.read_text(), flush=True)
                raise RuntimeError("native " + command + " failed")
        finally:
            if job.poll() is None:
                run(["docker", "stop", "--timeout", "5", name], timeout=30)
                job.wait(timeout=30)
    print(log_path.read_text(), end="", flush=True)


def healthy(directory, processes=None, *, repaired=False, endpoint=None, previous=()):
    deadline = time.monotonic() + 180
    while True:
        if processes:
            live(processes)
        try:
            tool(
                directory,
                "/app/native_api.py",
                "inventory",
                entrypoint="python",
                timeout=180,
            )
        except subprocess.CalledProcessError:
            # The exact native query failed; never reuse the preceding snapshot.
            # Printed stderr remains in the controller log for this attempt.
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
            continue
        values = json.loads((directory / "native-health.json").read_text())
        identity = member_identity(values, repaired=repaired)
        complete = not repaired or all(
            replacement_complete(v["replacement"], endpoint=endpoint, previous=previous)
            for v in values.values()
        )
        if identity is not None and complete:
            save(directory / "native-membership.json", identity)
            return identity
        if time.monotonic() > deadline:
            raise RuntimeError("native membership/replacement proof did not converge")
        time.sleep(1)


def fixture_check(*, initializing=False):
    value = json.loads((BASE / "fixture.json").read_text())
    if value["stage"] != "ready" and not (
        initializing and value["stage"] == "initializing"
    ):
        raise RuntimeError(
            "interrupted bootstrap requires diagnosis; no seed is overwritten"
        )
    current = inventory()
    if value["project"] != PROJECT or value["volumes"] != volumes(current):
        raise RuntimeError("fixture storage changed; retain evidence and diagnose")
    if any(not c["State"]["Running"] for c in current.values()):
        raise RuntimeError("fixture has a stopped member")
    native = healthy(BASE)
    if native != value["native_identity"]:
        raise RuntimeError(
            "native deployment/disk identity changed outside a declared replacement"
        )
    return current


def repair(directory, processes=None):
    healthy(directory, processes)
    run_job(directory, "tools", "/app/native_api.py", "heal", processes)


def verify(directory, processes=None, *, keep_golden=False):
    repair(directory, processes)
    if (directory / "churn.jsonl").exists():
        tool(directory, "verify")
    else:
        tool(
            directory,
            "-c",
            'import workload as w; w.save("reconciliation",w.Store().verify())',
            entrypoint="python",
        )
    if processes:
        live(processes)
    healthy(directory, processes, repaired=True)
    if not keep_golden:
        run_job(directory, "audit", "/app/physical.py", "snapshot", processes)
    run_job(directory, "audit", "/app/physical.py", "wait", processes)


def live_prefix(case, processes):
    live(processes)
    prefix = case / "live-repair-prefix"
    prefix.mkdir()
    for name in [
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "fixture",
        "physical-baseline",
        "native-membership",
    ]:
        path = case / (name + ".json")
        if path.exists():
            shutil.copy2(path, prefix / path.name)
    data = (case / "churn.jsonl").read_bytes()
    data = data[: data.rfind(b"\n") + 1]
    values = attempted_objects([json.loads(line) for line in data.splitlines()])
    count = sum(v["acked"] for v in values)
    if count < 5:
        raise RuntimeError("no complete live ACK prefix to repair")
    with (prefix / "churn.jsonl").open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    save(prefix / "prefix.json", {"ack_objects": count, "captured_at": time.time()})
    return prefix


def prepare_empty(volume, original):
    short = original["Labels"]["com.docker.compose.volume"]
    run(
        [
            "docker",
            "volume",
            "create",
            "--label",
            "com.docker.compose.project=" + PROJECT,
            "--label",
            "com.docker.compose.volume=" + short,
            volume,
        ]
    )
    code = 'from pathlib import Path; import os; p=Path("/empty/disk0"); p.mkdir(); os.chown("/empty",10001,10001); os.chown(p,10001,10001)'
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            volume + ":/empty",
            "--entrypoint",
            "python",
            PROJECT + "-tools:dev",
            "-c",
            code,
        ],
        timeout=30,
    )


def check_disk_change(before, after, mode, target):
    if before["deployment"] != after["deployment"]:
        raise RuntimeError("native deployment changed")
    changed = {n for n in before["disks"] if before["disks"][n] != after["disks"][n]}
    # Native admin format heal preserves the canonical slot UUID. A physical
    # replacement is proven separately by fencing, volume birth and bytes.
    if changed - ({target} if mode == "disk" else set()):
        raise RuntimeError("unexpected native disk UUID transition: " + str(changed))


def fault(mode):
    before = fixture_check()
    history()
    verify(BASE)
    native_before = json.loads((BASE / "native-membership.json").read_text())
    target = os.environ.get("FAULT_TARGET", "rustfs0")
    if target not in TARGETS or mode == "disk" and target == "s3":
        raise ValueError("target/mode not allowed")
    case = (
        BASE
        / f"fault-{mode}-{target}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    case.mkdir()
    save(
        case / "status.json",
        {
            "passed": False,
            "disk_removed": False,
            "cleanup_errors": [],
            "reason": "case not completed",
        },
    )
    save(
        case / "case.json",
        {
            "id": uuid.uuid4().hex,
            "mode": mode,
            "target": target,
            "controller_sha256": hashlib.sha256(CONTROLLER_SOURCE).hexdigest(),
            "tools_image": json.loads(
                run(["docker", "image", "inspect", PROJECT + "-tools:dev"])
            )[0]["Id"],
        },
    )
    (case / "controller.py").write_bytes(CONTROLLER_SOURCE)
    save(case / "before.json", before)
    free = json.loads((BASE / "physical-recovery.json").read_text())[
        "docker_data_free_bytes"
    ]
    if free < 6 * 1024**3:
        raise RuntimeError("less than 6 GiB Docker space; refusing injection")
    save(case / "capacity.json", {"free_bytes": free})
    for name in [
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "fixture",
        "physical-baseline",
        "native-membership",
    ]:
        path = BASE / (name + ".json")
        if path.exists():
            shutil.copy2(path, case / path.name)
    processes = {}
    names = {}
    injected = False
    success = False
    disk_removed = False
    with ExitStack() as stack:
        try:
            for role in ["writer", "reader"]:
                name = f"{PROJECT}-{role}-{uuid.uuid4().hex[:12]}"
                names[role] = name
                log = stack.enter_context((case / (role + ".log")).open("x"))
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
            phase(case, "injection")
            injected = True
            compose("kill", "-s", "SIGKILL", target)
            stopped = json.loads(run(["docker", "inspect", before[target]["Id"]]))[0]
            if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 137:
                raise RuntimeError("exact target did not exit 137")
            save(case / "stopped.json", stopped)
            diagnostics(case, {target: stopped}, "stopped-target")
            if mode == "disk":
                volume = next(
                    m["Name"]
                    for m in stopped["Mounts"]
                    if m["Type"] == "volume" and m["Destination"] == "/data"
                )
                original = json.loads(run(["docker", "volume", "inspect", volume]))[0]
                if original["Labels"]["com.docker.compose.project"] != PROJECT:
                    raise RuntimeError("foreign volume")
                compose("rm", "-f", target)
                compose("rm", "-f", "init")
                if stopped["Id"] in run(["docker", "ps", "-aq", "--no-trunc"]).split():
                    raise RuntimeError("old owner not fenced")
                run(["docker", "volume", "rm", volume])
                disk_removed = True
                save(case / "removed-disk.json", original)
                prepare_empty(volume, original)
            outage = time.time()
            phase(case, "outage")
            time.sleep(30)
            live(processes)
            if target != "s3":
                progress(case, processes, outage)
            phase(case, "repair")
            started = time.monotonic()
            compose(
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "180",
                target,
                timeout=240,
            )
            ready = time.monotonic() - started
            prefix = live_prefix(case, processes)
            if mode == "disk":
                # An unformatted endpoint cannot pass native membership yet.
                # The supported erasure-set operation formats its original slot.
                run_job(prefix, "tools", "/app/native_api.py", "format-set", processes)
                shutil.copy2(prefix / "heal-start.json", prefix / "set-heal-start.json")
                shutil.copy2(
                    prefix / "heal-status.json", prefix / "set-heal-status.json"
                )
            native_after = healthy(case, processes)
            check_disk_change(native_before, native_after, mode, target)
            shutil.copy2(
                case / "native-membership.json", prefix / "native-membership.json"
            )
            repair(prefix, processes)
            run_job(prefix, "audit", "/app/physical.py", "wait", processes)
            run_job(prefix, "tools", "/app/native_api.py", "read-required", processes)
            # Any automatic generations must be definitive/completed. The
            # explicit admin path is proven by its scoped canonical set outcome,
            # native format agreement, physical census and external whole bodies.
            healthy(case, processes, repaired=True)
            phase(case, "after")
            progress(case, processes, time.time())
            check_survivors(before, inventory(), target)
            (case / "stop-churn").touch()
            if processes["writer"].wait(timeout=30):
                raise RuntimeError("writer failed")
            reader = {"reader": processes["reader"]}
            live(reader)
            phase(case, "reconcile")
            verify(case, reader, keep_golden=True)
            phase(case, "verified")
            (case / "stop-reader").touch()
            if processes["reader"].wait(timeout=30):
                raise RuntimeError("reader failed")
            for role in ["writer", "reader"]:
                if not json.loads((case / (role + ".json")).read_text())[
                    "stopped_by_controller"
                ]:
                    raise RuntimeError(role + " exhausted its duration")
            after = inventory()
            check_survivors(before, after, target)
            save(case / "after.json", after)
            diagnostics(case, after, "final")
            native_final = json.loads((case / "native-membership.json").read_text())
            if native_final != native_after:
                raise RuntimeError(
                    "native identities changed during the repair barrier"
                )
            if mode == "disk":
                new_volumes = volumes(after)
                if new_volumes[volume] == original["CreatedAt"]:
                    raise RuntimeError("volume birth unchanged")
                fixture = json.loads((BASE / "fixture.json").read_text())
                fixture["volumes"] = new_volumes
                fixture["native_identity"] = native_after
                save(BASE / "fixture.json", fixture)
            result = report(case) | {
                "mode": mode,
                "target": target,
                "ready_seconds": ready,
                "survivors_unchanged": len(SERVICES) - 1,
                "live_prefix": json.loads((prefix / "prefix.json").read_text()),
                "live_prefix_recovery": json.loads(
                    (prefix / "physical-recovery.json").read_text()
                ),
                "live_prefix_readback": json.loads(
                    (prefix / "prefix-readback.json").read_text()
                ),
            }
            save(case / "result.json", result)
            print(json.dumps(result, indent=2), flush=True)
            success = True
        finally:
            errors = []
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
                                ["docker", "stop", "--timeout", "5", names[role]],
                                timeout=30,
                            )
                            process.wait(timeout=30)
                        except (subprocess.SubprocessError, OSError) as error:
                            errors.append(str(error))
            if injected and not success:
                try:
                    compose(
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        "180",
                        target,
                        timeout=240,
                    )
                    if mode == "disk" and disk_removed:
                        run_job(case, "tools", "/app/native_api.py", "format-set")
                    repair(case)
                    run_job(case, "audit", "/app/physical.py", "wait")
                except (subprocess.SubprocessError, OSError, RuntimeError) as error:
                    errors.append(str(error))
            history()
            save(
                case / "status.json",
                {
                    "passed": success,
                    "disk_removed": disk_removed,
                    "cleanup_errors": errors,
                },
            )
            if errors:
                raise RuntimeError("cleanup failed: " + "; ".join(errors))


def finish_bootstrap():
    fixture_check(initializing=True)
    if not (BASE / "seed.json").exists() or not (BASE / "manifest.json").exists():
        raise RuntimeError("partial seed requires diagnosis; refusing PUT replay")
    verify(BASE)
    fixture = json.loads((BASE / "fixture.json").read_text())
    fixture["stage"] = "ready"
    save(BASE / "fixture.json", fixture)


def recover_replacement():
    name = os.environ.get("RECOVERY_CASE", "")
    case = (BASE / name).resolve()
    if not name or case.parent != BASE or not case.name.startswith("fault-disk-"):
        raise ValueError("RECOVERY_CASE must name one retained failed disk case")
    status = json.loads((case / "status.json").read_text())
    detail = json.loads((case / "case.json").read_text())
    fixture = json.loads((BASE / "fixture.json").read_text())
    bound = json.loads((case / "fixture.json").read_text())
    if (
        status["passed"]
        or not status["disk_removed"]
        or detail["mode"] != "disk"
        or detail["target"] not in SERVICES - {"s3"}
        or fixture["id"] != bound["id"]
    ):
        raise RuntimeError("case does not prove a failed replacement of this fixture")
    before = json.loads((case / "before.json").read_text())
    current = inventory()
    target = detail["target"]
    check_survivors(before, current, target)
    if before[target]["Id"] in run(["docker", "ps", "-aq", "--no-trunc"]).split():
        raise RuntimeError("original owner is still registered")
    removed = json.loads((case / "removed-disk.json").read_text())
    current_volumes = volumes(current)
    if set(current_volumes) != set(bound["volumes"]):
        raise RuntimeError("unexpected native volume set during recovery")
    changed = {n for n in current_volumes if current_volumes[n] != bound["volumes"][n]}
    if (
        changed != {removed["Name"]}
        or removed["Labels"]["com.docker.compose.project"] != PROJECT
    ):
        raise RuntimeError("volume births do not prove only the declared replacement")
    history()
    recovery = case / ("recovery-" + uuid.uuid4().hex)
    recovery.mkdir()
    for kind in [
        "fixture",
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
    ]:
        path = BASE / (kind + ".json")
        if path.exists():
            shutil.copy2(path, recovery / path.name)
    shutil.copy2(case / "physical-baseline.json", recovery / "physical-baseline.json")
    save(
        recovery / "recovery-scope.json",
        {
            "failed_case": case.name,
            "fixture": fixture["id"],
            "application_put_replay": False,
            "method": "native admin erasure-set format/heal, pool0/set0",
        },
    )
    run_job(recovery, "tools", "/app/native_api.py", "format-set")
    shutil.copy2(recovery / "heal-start.json", recovery / "set-heal-start.json")
    shutil.copy2(recovery / "heal-status.json", recovery / "set-heal-status.json")
    native = healthy(recovery)
    check_disk_change(bound["native_identity"], native, "disk", target)
    verify(recovery, keep_golden=True)
    fixture["volumes"] = current_volumes
    fixture["native_identity"] = native
    save(BASE / "fixture.json", fixture)
    verify(BASE)
    save(
        recovery / "result.json",
        {
            "recovered": True,
            "failed_case_stays_failed": True,
            "fixture": fixture["id"],
            "native_identity": native,
        },
    )
    print(
        "Recovered",
        case.name,
        "without PUT replay; original case remains failed",
        flush=True,
    )


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
            raise RuntimeError("unbound old oracle; diagnose instead of reseeding")
        current = inventory()
        storage = volumes(current)
        native = healthy(BASE)
        witness = {
            "id": str(uuid.uuid4()),
            "project": PROJECT,
            "stage": "initializing",
            "volumes": storage,
            "native_identity": native,
        }
        save(BASE / "bootstrap.json", {"containers": current, "witness": witness})
        save(BASE / "fixture.json", witness)
        tool(BASE, "create")
        tool(BASE, "seed")
        finish_bootstrap()
        return
    if action == "recover-replacement":
        recover_replacement()
    elif action == "recover-bootstrap":
        if json.loads((BASE / "fixture.json").read_text())["stage"] != "initializing":
            raise RuntimeError("only an interrupted bootstrap may be resumed")
        finish_bootstrap()
    elif action in ["verify", "repair"]:
        fixture_check()
        history()
        verify(BASE)
    elif action in ["process", "disk"]:
        fault(action)
    else:
        raise ValueError("unknown action")


if __name__ == "__main__":
    main()
