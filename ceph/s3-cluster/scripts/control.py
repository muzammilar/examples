"""Ceph-native recovery under independent, externally witnessed S3 traffic."""

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

from native_state import identities, survivors
from witness import attempted_objects, records
from witness_archive import archive_oracles

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_SOURCE = Path(__file__).read_bytes()
BASE = (ROOT / os.environ.get("RESULTS_DIR", "results/recovery")).resolve()
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "ceph-s3-cluster")
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
SERVICES = {"guests", "s3"}
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
        "DURATION": os.environ.get("FAULT_DURATION", "1800"),
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
            "native-state",
            "prefix-readback",
            "reconciliation",
        },
    )


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
        "native-state",
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


def inventory():
    ids = compose("ps", "-aq").split()
    values = json.loads(run(["docker", "inspect", *ids])) if ids else []
    found = {}
    for value in values:
        labels = value["Config"]["Labels"]
        service = labels["com.docker.compose.service"]
        if service not in SERVICES:
            continue
        if labels["com.docker.compose.project"] != PROJECT or service in found:
            raise RuntimeError("foreign/duplicate infrastructure")
        found[service] = value
    if set(found) != SERVICES:
        raise RuntimeError("incomplete infrastructure")
    return found


def volumes(values):
    names = {
        m["Name"] for v in values.values() for m in v["Mounts"] if m["Type"] == "volume"
    }
    if len(names) != 1:
        raise RuntimeError("expected the one owned guest-state volume")
    result = json.loads(run(["docker", "volume", "inspect", *names]))
    if any(
        (v.get("Labels") or {}).get("com.docker.compose.project") != PROJECT
        for v in result
    ):
        raise RuntimeError("foreign guest disks")
    return {v["Name"]: v["CreatedAt"] for v in result}


def job(directory, service, args, processes=None, timeout=1900):
    name = PROJECT + "-check-" + uuid.uuid4().hex[:12]
    path = directory / ("job-" + uuid.uuid4().hex + ".log")
    options = ["run", "--rm", "-T", "--name", name]
    if service == "tools":
        options += ["--user", USER, "--entrypoint", "python"]
    with path.open("x") as output:
        process = subprocess.Popen(
            COMPOSE + options + [service] + args,
            env=env_for(directory),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        end = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if processes:
                    live(processes)
                if time.monotonic() >= end:
                    raise RuntimeError("native/verifier deadline exceeded")
                time.sleep(0.25)
            if process.returncode:
                print(path.read_text()[-6000:], flush=True)
                raise RuntimeError("native/verifier failed: " + " ".join(args))
        finally:
            if process.poll() is None:
                run(["docker", "stop", "--timeout", "5", name], timeout=30)
                process.wait(timeout=30)


def native(directory, script, *args, processes=None):
    job(directory, "control", ["/opt/lab/native/" + script, *args], processes)


def snapshot(directory, processes=None):
    native(directory, "observe.py", "snapshot", processes=processes)
    value = json.loads((directory / "native-state.json").read_text())
    identities(value)
    return value


def fixture_check():
    fixture = json.loads((BASE / "fixture.json").read_text())
    current = inventory()
    if (
        fixture["stage"] != "ready"
        or fixture["project"] != PROJECT
        or fixture["volumes"] != volumes(current)
    ):
        raise RuntimeError(
            "fixture is unready or guest disks changed; preserve and diagnose"
        )
    if identities(snapshot(BASE)) != fixture["native_identity"]:
        raise RuntimeError(
            "native cluster/OSD identities changed outside a declared recovery"
        )
    return current


def verify(directory, processes=None):
    native(directory, "recovery.py", "audit", processes=processes)
    if (directory / "churn.jsonl").exists():
        tool(directory, "verify")
    else:
        tool(
            directory,
            "-c",
            'import workload as w; w.save("reconciliation",w.Store().verify())',
            entrypoint="python",
        )
    state = snapshot(directory, processes)
    return state


def diagnostics(directory, label):
    target = directory / ("diagnostics-" + label)
    target.mkdir()
    native(target, "observe.py", "logs")
    with (target / "containers.log").open("x") as log:
        subprocess.run(
            COMPOSE + ["logs", "--no-color", "--tail", "2000"],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )


def up():
    retire_removed_fixture()
    if (BASE / "fixture.json").exists():
        fixture_check()
        history()
        verify(BASE)
        return
    if (BASE / "manifest.json").exists():
        raise RuntimeError("unbound historical oracle; no automatic reseeding")
    native(BASE, "bootstrap.py")
    compose("up", "-d", "--wait", "s3")
    current = inventory()
    state = snapshot(BASE)
    save(
        BASE / "fixture.json",
        {
            "id": str(uuid.uuid4()),
            "stage": "initializing",
            "project": PROJECT,
            "volumes": volumes(current),
            "native_identity": identities(state),
        },
    )
    tool(BASE, "create")
    tool(BASE, "seed")
    finish_bootstrap()


def finish_bootstrap():
    fixture = json.loads((BASE / "fixture.json").read_text())
    if (
        fixture["stage"] != "initializing"
        or fixture["volumes"] != volumes(inventory())
        or not (BASE / "seed.json").exists()
        or not (BASE / "manifest.json").exists()
    ):
        raise RuntimeError(
            "bootstrap recovery requires the same disks and a fully witnessed seed"
        )
    history()
    state = verify(BASE)
    if identities(state) != fixture["native_identity"]:
        raise RuntimeError("bootstrap native identity changed")
    fixture["stage"] = "ready"
    save(BASE / "fixture.json", fixture)


def fault(mode):
    if mode not in {"guest", "disk", "rebuilt", "router"}:
        raise ValueError(mode)
    before = fixture_check()
    history()
    native_before = verify(BASE)
    fixture = json.loads((BASE / "fixture.json").read_text())
    if mode == "rebuilt" and not fixture.get("replaced_osd"):
        raise RuntimeError("rebuilt-member crash requires a completed disk replacement")
    case = BASE / (
        f"fault-{mode}-{time.strftime('%Y%m%dT%H%M%S')}-" + uuid.uuid4().hex[:8]
    )
    case.mkdir()
    save(case / "status.json", {"passed": False, "reason": "case not completed"})
    save(
        case / "case.json",
        {
            "id": uuid.uuid4().hex,
            "mode": mode,
            "controller_sha256": hashlib.sha256(CONTROLLER_SOURCE).hexdigest(),
        },
    )
    (case / "controller.py").write_bytes(CONTROLLER_SOURCE)
    for name in [
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "fixture",
        "native-state",
    ]:
        path = BASE / (name + ".json")
        if path.exists():
            shutil.copy2(path, case / path.name)
    save(case / "before.json", before)
    diagnostics(case, "before")
    processes, names = {}, {}
    injected = passed = False
    recovery_mode = "disk" if mode == "disk" else "process"
    with ExitStack() as stack:
        try:
            for role in ["writer", "reader"]:
                names[role] = PROJECT + "-" + role + "-" + uuid.uuid4().hex[:12]
                output = stack.enter_context((case / (role + ".log")).open("x"))
                processes[role] = subprocess.Popen(
                    COMPOSE
                    + [
                        "run",
                        "--rm",
                        "-T",
                        "--name",
                        names[role],
                        "--user",
                        USER,
                        "--entrypoint",
                        "python",
                        "tools",
                        "/app/traffic.py",
                        role,
                    ],
                    env=env_for(case),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            phase(case, "baseline")
            progress(case, processes, 0)
            time.sleep(5)
            live(processes)
            phase(case, "injection")
            injected = True
            if mode == "router":
                compose("kill", "-s", "SIGKILL", "s3")
                stopped = json.loads(run(["docker", "inspect", before["s3"]["Id"]]))[0]
                if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 137:
                    raise RuntimeError("router did not exit 137")
                save(case / "stopped.json", stopped)
            else:
                native(case, "fault.py", "inject", recovery_mode, processes=processes)
            phase(case, "outage")
            started_outage = time.time()
            time.sleep(30)
            live(processes)
            if mode != "router":
                progress(case, processes, started_outage)
            phase(case, "repair")
            if mode == "router":
                compose("up", "-d", "--no-deps", "--wait", "s3")
            else:
                native(case, "fault.py", "recover", recovery_mode, processes=processes)
            prefix = live_prefix(case, processes)
            native(prefix, "recovery.py", "audit", processes=processes)
            job(prefix, "tools", ["/app/prefix_readback.py"], processes)
            native_after = snapshot(case, processes)
            replacement = (
                json.loads((case / "replacement.json").read_text())["osd"]
                if mode == "disk"
                else None
            )
            survivors(
                native_before,
                native_after,
                guest="0" if mode in {"guest", "rebuilt"} else None,
                replaced_osd=str(replacement) if replacement is not None else None,
            )
            check_survivors(before, inventory(), "s3" if mode == "router" else None)
            phase(case, "after")
            progress(case, processes, time.time())
            (case / "stop-churn").touch()
            if processes["writer"].wait(timeout=30):
                raise RuntimeError("writer failed")
            phase(case, "reconcile")
            native_final = verify(case, {"reader": processes["reader"]})
            survivors(native_after, native_final)
            phase(case, "verified")
            (case / "stop-reader").touch()
            if processes["reader"].wait(timeout=30):
                raise RuntimeError("reader failed")
            for role in ["writer", "reader"]:
                if not json.loads((case / (role + ".json")).read_text())[
                    "stopped_by_controller"
                ]:
                    raise RuntimeError(role + " expired before recovery completion")
            check_survivors(before, inventory(), "s3" if mode == "router" else None)
            if mode == "disk":
                fixture["native_identity"] = identities(native_final)
                fixture["replaced_osd"] = str(replacement)
                save(BASE / "fixture.json", fixture)
            save(
                case / "result.json",
                report(case)
                | {
                    "mode": mode,
                    "live_prefix": json.loads((prefix / "prefix.json").read_text()),
                    "prefix_readback": json.loads(
                        (prefix / "prefix-readback.json").read_text()
                    ),
                    "native_audit": json.loads(
                        (prefix / "native-audit.json").read_text()
                    ),
                },
            )
            diagnostics(case, "after")
            passed = True
        finally:
            errors = []
            for role, process in processes.items():
                if process.poll() is None:
                    (
                        case / ("stop-churn" if role == "writer" else "stop-reader")
                    ).touch()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        run(
                            ["docker", "stop", "--timeout", "5", names[role]],
                            timeout=30,
                        )
                        process.wait(timeout=30)
            if injected and not passed:
                try:
                    if mode == "router":
                        compose("up", "-d", "--no-deps", "--wait", "s3")
                    else:
                        native(case, "fault.py", "recover", recovery_mode)
                except (subprocess.SubprocessError, OSError, RuntimeError) as error:
                    errors.append(str(error))
            history()
            save(case / "status.json", {"passed": passed, "cleanup_errors": errors})
            if errors:
                raise RuntimeError("cleanup failed: " + "; ".join(errors))


def teardown():
    fixture = (
        json.loads((BASE / "fixture.json").read_text())
        if (BASE / "fixture.json").exists()
        else None
    )
    if fixture and fixture["project"] != PROJECT:
        raise RuntimeError("foreign teardown witness")
    compose("--profile", "tools", "down", "--volumes", "--remove-orphans")
    tags = [PROJECT + "-" + role + ":dev" for role in ["tools", "guests"]]
    for tag in tags:
        if run(["docker", "image", "ls", "-q", tag]).strip():
            run(["docker", "image", "rm", tag])
    label = "label=com.docker.compose.project=" + PROJECT
    remaining = {
        kind: run(["docker", command, *options, "--filter", label]).split()
        for kind, command, options in [
            ("containers", "ps", ["-aq"]),
            ("volumes", "volume", ["ls", "-q"]),
            ("networks", "network", ["ls", "-q"]),
        ]
    }
    remaining["build_tag"] = [
        tag for tag in tags if run(["docker", "image", "ls", "-q", tag]).strip()
    ]
    if any(remaining.values()):
        raise RuntimeError("owned resources remain: " + str(remaining))
    save(
        BASE / "teardown.json",
        {
            "fixture_id": fixture["id"] if fixture else None,
            "project": PROJECT,
            "finished": True,
            "remaining": remaining,
        },
    )


def recover_replacement():
    name = os.environ.get("RECOVERY_CASE", "")
    case = (BASE / name).resolve()
    if not name or case.parent != BASE or not case.name.startswith("fault-disk-"):
        raise ValueError("RECOVERY_CASE must name one retained failed disk case")
    if json.loads((case / "status.json").read_text())["passed"]:
        raise RuntimeError("case is not failed")
    fixture = json.loads((BASE / "fixture.json").read_text())
    bound = json.loads((case / "fixture.json").read_text())
    if fixture["id"] != bound["id"] or volumes(inventory()) != fixture["volumes"]:
        raise RuntimeError("case/guest volume does not belong to the current fixture")
    native(case, "fault.py", "recover", "disk")
    plan = json.loads((case / "replacement.json").read_text())
    history()
    directory = case / ("recovery-" + uuid.uuid4().hex)
    directory.mkdir()
    for kind in [
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "fixture",
    ]:
        path = BASE / (kind + ".json")
        if path.exists():
            shutil.copy2(path, directory / path.name)
    state = verify(directory)
    expected = json.loads(json.dumps(bound["native_identity"]))
    expected["osds"][str(plan["osd"])]["uuid"] = plan["new_osd_fsid"]
    if identities(state) != expected:
        raise RuntimeError("recovery changed an unrelated cluster/OSD identity")
    fixture["native_identity"] = expected
    fixture["replaced_osd"] = str(plan["osd"])
    save(BASE / "fixture.json", fixture)
    verify(BASE)
    save(
        directory / "result.json",
        {
            "recovered": True,
            "application_put_replay": False,
            "failed_case_stays_failed": True,
        },
    )


def main():
    os.chdir(ROOT)
    BASE.mkdir(parents=True, exist_ok=True)
    action = sys.argv[1]
    if action == "up":
        up()
    elif action == "recover-bootstrap":
        finish_bootstrap()
    elif action == "recover-replacement":
        recover_replacement()
    elif action == "docker-down":
        teardown()
    elif action in {"guest", "disk", "rebuilt", "router"}:
        fault(action)
    elif action in {"verify", "repair"}:
        fixture_check()
        history()
        verify(BASE)
    else:
        raise ValueError(action)


if __name__ == "__main__":
    main()
