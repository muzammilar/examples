"""Serialize this example and record native container resource counters off the SUT."""

import fcntl
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid
import hashlib
import shutil
import resource

import resources

ROOT = Path(__file__).resolve().parent
PROJECT = "zfs-storage-lab"
PROBE = r"""
import json,os
from pathlib import Path
p=Path('/sys/fs/cgroup')
r={k:(p/k).read_text() for k in ['cpu.stat','memory.current','memory.peak','io.stat']}
s=Path('/state')
f=[p.stat() for p in s.rglob('*') if p.is_file()] if s.exists() else []
r['state_logical_bytes']=sum(p.st_size for p in f)
r['state_allocated_bytes']=sum(p.st_blocks*512 for p in f)
r['captured_ns']=__import__('time').time_ns()
print(json.dumps(r))
"""


def docker(*arguments):
    return subprocess.check_output(
        ["docker", *arguments], text=True, cwd=ROOT, timeout=30
    )


def inventory():
    identifiers = docker(
        "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT
    ).split()
    if not identifiers:
        return []
    values = []
    for identifier in identifiers:
        try:
            values.extend(json.loads(docker("inspect", identifier)))
        except subprocess.CalledProcessError:
            # --rm one-shot containers may disappear between list and inspect.
            # Require a healthy second listing proving this exact ID is absent.
            current = docker(
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
            ).split()
            if any(value.startswith(identifier) for value in current):
                raise
    for value in values:
        labels = value["Config"]["Labels"]
        if (
            labels.get("com.docker.compose.project") != PROJECT
            or Path(labels["com.docker.compose.project.working_dir"]).resolve() != ROOT
        ):
            raise RuntimeError("Docker resource ownership mismatch")
    return values


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def execute(arguments, run):
    if arguments in (["docker-up"], ["docker-up-source"], ["docker-up-target"]):
        services = (
            ["source", "target"]
            if arguments == ["docker-up"]
            else [arguments[0].removeprefix("docker-up-")]
        )
        return subprocess.run(
            ["docker", "compose", "up", "-d", "--wait", *services],
            cwd=ROOT,
            check=False,
        )
    if arguments != ["docker-down"]:
        return subprocess.run(
            ["docker", "compose", "run", "--rm", "runner", *arguments],
            cwd=ROOT,
            check=False,
        )
    members = inventory()
    if members:
        if any(not m["State"]["Running"] for m in members):
            raise RuntimeError(
                "stopped owned storage remains; start its guests before verified archival/teardown"
            )
        subprocess.run(
            ["docker", "compose", "run", "--rm", "runner", "archive-pools"],
            cwd=ROOT,
            check=True,
        )
    elif docker(
        "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=" + PROJECT
    ).strip():
        raise RuntimeError(
            "owned volumes have no reachable owner; preserve them until archived"
        )
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "down",
            "--volumes",
            "--remove-orphans",
        ],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode:
        return completed
    remaining = {
        "containers": [m["Id"] for m in inventory()],
        "volumes": docker(
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ).split(),
        "networks": docker(
            "network",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ).split(),
    }
    save(run / "teardown-verification.json", remaining)
    if any(remaining.values()):
        raise RuntimeError("owned resources remain after teardown")
    tags = docker(
        "image", "ls", "-q", "--filter", "reference=zfs-storage-lab:dev"
    ).split()
    if tags:
        image = json.loads(docker("image", "inspect", "zfs-storage-lab:dev"))[0]
        if image["Config"].get("Labels", {}).get("org.examples.owner") != PROJECT:
            raise RuntimeError("refuse removal of image without matching owner label")
        docker("image", "rm", "zfs-storage-lab:dev")
    for name in ("id_ed25519", "id_ed25519.pub"):
        (ROOT / "state/ssh" / name).unlink(missing_ok=True)
    directory = ROOT / "state/ssh"
    if directory.exists():
        directory.rmdir()
    return completed


def main():
    wall_started, cpu_started = time.monotonic(), time.process_time()
    children_started = resource.getrusage(resource.RUSAGE_CHILDREN)
    results = ROOT / os.environ.get("RESULTS_DIR", "results/manual")
    results = results.resolve()
    if not results.is_relative_to((ROOT / "results").resolve()):
        raise RuntimeError("RESULTS_DIR must stay inside ignored example results/")
    results.mkdir(parents=True, exist_ok=True)
    state = ROOT / "state"
    state.mkdir(exist_ok=True)
    with (state / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = results / "runs" / uuid.uuid4().hex
        run.mkdir(parents=True)
        members = inventory()
        save(
            run / "environment.json",
            {
                "started_ns": time.time_ns(),
                "host_system": platform.system(),
                "host_architecture": platform.machine(),
                "host_kernel": platform.release(),
                "docker": json.loads(docker("info", "--format", "{{json .}}")),
                "members": [
                    {
                        "id": m["Id"],
                        "image_id": m["Image"],
                        "image": m["Config"]["Image"],
                        "role": m["Config"]["Labels"]["com.docker.compose.service"],
                        "cpu_limit": m["HostConfig"]["NanoCpus"] / 1e9,
                        "memory_limit_bytes": m["HostConfig"]["Memory"],
                    }
                    for m in members
                ],
                "source_revision": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True, cwd=ROOT
                ).strip(),
                "scope": "One-host QEMU TCG guests; all roles share Docker VM and host. Other independent functional labs may run concurrently. Diagnostic, not a performance comparison. Images and exact resources recorded; guest versions in versions.json.",
            },
        )
        stopped = threading.Event()
        failures = []

        def sample():
            with (run / "resources.jsonl").open("x") as output:
                while not stopped.is_set():
                    try:
                        for member in inventory():
                            if not member["State"]["Running"]:
                                continue
                            started = time.time_ns()
                            try:
                                counters = json.loads(
                                    docker("exec", member["Id"], "python3", "-c", PROBE)
                                )
                                record = {
                                    "started_ns": started,
                                    "ended_ns": time.time_ns(),
                                    "id": member["Id"],
                                    "role": member["Config"]["Labels"][
                                        "com.docker.compose.service"
                                    ],
                                    "counters": counters,
                                }
                            except (
                                subprocess.SubprocessError,
                                ValueError,
                            ) as exception:
                                record = {
                                    "started_ns": started,
                                    "ended_ns": time.time_ns(),
                                    "id": member["Id"],
                                    "unavailable": str(exception),
                                }
                            output.write(json.dumps(record) + "\n")
                        output.flush()
                        os.fsync(output.fileno())
                    except Exception as exception:
                        failures.append(str(exception))
                        return
                    stopped.wait(1)

        save(
            run / "sources.json",
            {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [
                    ROOT / "run.py",
                    ROOT / "resources.py",
                    ROOT / "Makefile",
                    ROOT / "docker-compose.yml",
                    ROOT / "vm/Dockerfile",
                    ROOT / "vm/boot.py",
                    *sorted((ROOT / "app").glob("*.py")),
                ]
            },
        )
        save(
            run / "host-storage.json",
            dict(
                zip(
                    ("total_bytes", "used_bytes", "free_bytes"), shutil.disk_usage(ROOT)
                )
            ),
        )
        previous_cases = set((results / "cases").glob("*/result.json"))
        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        started = time.monotonic()
        result = None
        failure = None
        try:
            result = execute(sys.argv[1:], run)
        except BaseException as exception:
            failure = {"type": type(exception).__name__, "error": str(exception)}
            raise
        finally:
            stopped.set()
            thread.join(40)
            usage = resource.getrusage(resource.RUSAGE_SELF)
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            elapsed, cpu = (
                time.monotonic() - wall_started,
                time.process_time() - cpu_started,
            )
            save(
                run / "host-controller-resources.json",
                {
                    "wall_seconds": elapsed,
                    "self_cpu_core_seconds": cpu,
                    "self_cpu_percent_one_core_100": cpu / elapsed * 100,
                    "children_cpu_core_seconds": children.ru_utime
                    + children.ru_stime
                    - children_started.ru_utime
                    - children_started.ru_stime,
                    "process_peak_rss_bytes": usage.ru_maxrss
                    * (1 if platform.system() == "Darwin" else 1024),
                    "scope": "Host Python controller across all threads, plus separately reaped Docker CLI child CPU. Includes sampling/metadata/command costs after main starts; peak RSS is process lifetime. Native Docker/QEMU cgroup CPU is a separate layer; do not sum counters as physical-device usage.",
                },
            )
            save(
                run / "command-outcome.json",
                {
                    "exit_code": result.returncode if result else None,
                    "failure": failure,
                    "ended_ns": time.time_ns(),
                    "sampler_stopped": not thread.is_alive(),
                },
            )
        save(
            run / "completion.json",
            {
                "ended_ns": time.time_ns(),
                "duration_s": time.monotonic() - started,
                "exit_code": result.returncode if result else None,
                "sampling_errors": failures,
                "sampler_stopped": not thread.is_alive(),
            },
        )
        records = [
            json.loads(line)
            for line in (run / "resources.jsonl").read_text().splitlines()
        ]
        save(run / "resource-summary.json", resources.summarize(records))
        phase_summaries = {}
        for path in set((results / "cases").glob("*/result.json")) - previous_cases:
            case = json.loads(path.read_text())
            if "phase_boundaries_ns" in case:
                phase_summaries[path.parent.name] = resources.summarize_phases(
                    records, case["phase_boundaries_ns"]
                )
        save(run / "case-phase-resources.json", phase_summaries)
        if failures or thread.is_alive():
            raise RuntimeError("resource recorder failed; raw evidence preserved")
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
