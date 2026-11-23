"""SeaweedFS-specific fault control; independent SDK traffic and native audits."""

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
BASE = Path(os.environ.get("RESULTS_DIR", "results/recovery")).resolve()
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "seaweedfs-s3-cluster")
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
SERVICES = {
    *[f"master{i}" for i in range(3)],
    *[f"etcd{i}" for i in range(3)],
    *[f"volume{i}" for i in range(9)],
    *[f"filer{i}" for i in range(3)],
    *[f"s3api{i}" for i in range(3)],
    "s3",
}
TARGETS = {
    *[f"volume{i}" for i in range(9)],
    "filer0",
    "s3api0",
    *[f"etcd{i}" for i in range(3)],
    *[f"master{i}" for i in range(3)],
}
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
        "DURATION": os.environ.get("FAULT_DURATION", "600"),
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
    # The upstream image declares /data even on stateless filers/gateways.
    # Those anonymous mounts are proven by their exact owned-container mounts;
    # they have Docker's anonymous marker, not Compose project labels.
    for volume in values:
        labels = volume.get("Labels") or {}
        if (
            labels.get("com.docker.compose.project") != PROJECT
            and "com.docker.volume.anonymous" not in labels
        ):
            raise RuntimeError("foreign volume")
    return {v["Name"]: v["CreatedAt"] for v in values}


def fixture_check():
    value = json.loads((BASE / "fixture.json").read_text())
    current = inventory()
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
        for value in attempted_objects(records(case / "churn.jsonl")):
            key = value["object"]["key"]
            if key in seen:
                raise RuntimeError("repeated object across histories: " + key)
            seen.add(key)
            values.append(value)
    save(BASE / "fault-history.json", values)
    return values


def native(directory, mode):
    tool(directory, "/app/seaweed_audit.py", mode, entrypoint="python")


def metadata(directory):
    print(
        run([sys.executable, "scripts/metadata_audit.py"], env=env_for(directory)),
        end="",
        flush=True,
    )


def bytes_audit(directory):
    tool(directory, "/app/seaweed_bytes.py", entrypoint="python")


def repair(directory):
    commands = "lock\nvolume.fix.replication -apply\nunlock\n"
    output = compose(
        "exec",
        "-T",
        "master0",
        "weed",
        "shell",
        "-master=master0:9333,master1:9333,master2:9333",
        "-filer=filer0:8888",
        input=commands,
    )
    (directory / "repair.log").write_text(output)
    print(output, end="", flush=True)


def verify(directory):
    tool(directory, "verify" if (directory / "churn.jsonl").exists() else "probe")
    if not (directory / "churn.jsonl").exists():
        # Shared verifier's fault mode requires a current journal; a permanent
        # empty one is not invented. Invoke the same full oracle without it.
        tool(
            directory,
            "-c",
            'import workload as w; w.save("reconciliation", w.Store().verify())',
            entrypoint="python",
        )
    native(directory, "snapshot")
    native(directory, "wait")
    bytes_audit(directory)
    metadata(directory)


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


def discover_leader(role):
    if role == "master":
        value = json.loads(
            compose(
                "exec",
                "-T",
                "master0",
                "wget",
                "-q",
                "-O",
                "-",
                "http://127.0.0.1:9333/cluster/status",
            )
        )
        target = value["Leader"].split(":")[0]
    elif role == "etcd":
        value = json.loads(
            compose(
                "exec",
                "-T",
                "etcd0",
                "/usr/local/bin/etcdctl",
                "--endpoints=http://etcd0:2379,http://etcd1:2379,http://etcd2:2379",
                "endpoint",
                "status",
                "-w",
                "json",
            )
        )
        leaders = {v["Status"]["leader"] for v in value}
        if len(value) != 3 or len(leaders) != 1:
            raise RuntimeError("metadata leadership is not converged")
        matches = [
            v
            for v in value
            if v["Status"]["header"]["member_id"] == next(iter(leaders))
        ]
        if len(matches) != 1:
            raise RuntimeError("metadata leader is not a known member")
        target = matches[0]["Endpoint"].split("//")[1].split(":")[0]
    else:
        raise ValueError("unknown native role")
    if target not in {f"{role}{i}" for i in range(3)}:
        raise RuntimeError("unknown native leader")
    return target, value


def data_target(placement):
    owners = sorted(
        {
            r["node"]
            for replicas in placement.values()
            for r in replicas
            if r["files"] > 0
        }
    )
    if not owners or any(node not in range(9) for node in owners):
        raise RuntimeError("no known nonempty data owner")
    return f"volume{owners[0]}"


def fault(mode):
    before = fixture_check()
    history()
    native(BASE, "snapshot")
    native(BASE, "wait")
    native_role = os.environ.get("FAULT_ROLE")
    placement = json.loads((BASE / "volume-placement.json").read_text())
    target_name, leader = (
        discover_leader(native_role)
        if native_role
        else (os.environ.get("FAULT_TARGET") or data_target(placement), None)
    )
    if (
        target_name not in TARGETS
        or mode == "disk"
        and not target_name.startswith("volume")
    ):
        raise ValueError("target/mode is not allowlisted")
    case = (
        BASE
        / f"fault-{mode}-{target_name}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    case.mkdir()
    save(
        case / "case.json",
        {"id": uuid.uuid4().hex, "mode": mode, "target": target_name},
    )
    save(case / "before.json", before)
    if native_role:
        save(case / "leader.json", leader)
    free_kib = int(
        compose("exec", "-T", "volume0", "df", "-Pk", "/data")
        .splitlines()[-1]
        .split()[3]
    )
    if free_kib < 6 * 1024 * 1024:
        raise RuntimeError("less than 6 GiB free in Docker; refusing fault injection")
    save(case / "capacity.json", {"free_kib": free_kib})
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
            if native_role and discover_leader(native_role)[0] != target_name:
                raise RuntimeError("leader changed before injection; no fault applied")
            if target_name.startswith("volume"):
                node = int(target_name.removeprefix("volume"))
                placement = json.loads((BASE / "volume-placement.json").read_text())
                owned = [
                    v
                    for v, replicas in placement.items()
                    if any(r["node"] == node and r["files"] > 0 for r in replicas)
                ]
                if not owned:
                    raise RuntimeError("target data server owns no nonempty volume")
                save(case / "affected-volumes.json", owned)
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
                save(case / "removed-disk.json", original)
            phase(case, "outage")
            time.sleep(30)
            live(processes)
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
            repair(case)
            native(case, "wait")
            shutil.copy2(
                case / "physical-recovery.json", case / "preexisting-recovery.json"
            )
            repaired_at = time.time()
            phase(case, "after")
            progress(case, processes, repaired_at)
            check_survivors(before, inventory(), target_name)
            (case / "stop-churn").touch()
            if processes["writer"].wait(timeout=30):
                raise RuntimeError("writer failed")
            live({"reader": processes["reader"]})
            # Writer is stopped, so inventory and byte checks cover a stable set.
            verify(case)
            live({"reader": processes["reader"]})
            phase(case, "verified")
            (case / "stop-reader").touch()
            if processes["reader"].wait(timeout=30):
                raise RuntimeError("reader failed")
            after = inventory()
            check_survivors(before, after, target_name)
            save(case / "after.json", after)
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
    current = set(run(["docker", "volume", "ls", "-q"]).split())
    remaining = {
        "containers": run(["docker", "ps", "-aq", "--filter", label]).split(),
        "volumes": run(["docker", "volume", "ls", "-q", "--filter", label]).split(),
        "networks": run(["docker", "network", "ls", "-q", "--filter", label]).split(),
        "build_tag": run(["docker", "image", "ls", "-q", tag]).split(),
        "witnessed_volumes": sorted(
            current & set(fixture["volumes"] if fixture else [])
        ),
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
        or set(value["remaining"])
        != {"containers", "volumes", "networks", "build_tag", "witnessed_volumes"}
        or any(value["remaining"].values())
    ):
        raise RuntimeError("invalid teardown proof; preserving the old witness")
    archive_oracles(
        BASE,
        extra={
            "fixture",
            "bootstrap",
            "teardown",
            "reconciliation",
        },
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
            raise RuntimeError(
                "unbound existing oracle; select a new RESULTS_DIR or diagnose the old fixture"
            )
        before = inventory()
        storage = volumes(before)
        save(BASE / "bootstrap.json", {"containers": before, "volumes": storage})
        tool(BASE, "create")
        tool(BASE, "seed")
        save(
            BASE / "fixture.json",
            {"id": str(uuid.uuid4()), "project": PROJECT, "volumes": storage},
        )
        return
    if action == "verify":
        fixture_check()
        history()
        verify(BASE)
    elif action in ["process", "disk"]:
        fault(action)
    else:
        raise ValueError("unknown action")


if __name__ == "__main__":
    main()
