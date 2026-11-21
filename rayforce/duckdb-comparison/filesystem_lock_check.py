"""Verify the storage's standard flock contract before testing native ownership."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import experiment as bench
import ingestion
from journal import inspect_owned


HOLDER = """
import fcntl,json,os,time
f=open('/state/probe.lock','a+')
fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
print(json.dumps({'ready':True,'stat':list(os.fstat(f.fileno())), 'mountinfo':open('/proc/self/mountinfo').read()}),flush=True)
time.sleep(300)
"""
TRY = """
import fcntl,json,os,pathlib,resource,time
started,cpu=time.monotonic_ns(),time.process_time_ns()
f=open('/state/probe.lock','a+')
try:
    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    blocked=False
except BlockingIOError:
    blocked=True
usage=resource.getrusage(resource.RUSAGE_SELF)
print(json.dumps({'blocked':blocked,'stat':list(os.fstat(f.fileno())),
 'file_bytes':os.fstat(f.fileno()).st_size,'allocated_bytes':os.fstat(f.fileno()).st_blocks*512,
 'wall_ns':time.monotonic_ns()-started,'cpu_ns':time.process_time_ns()-cpu,
 'process_peak_rss_bytes':usage.ru_maxrss*1024,
 'cgroup':{p:pathlib.Path('/sys/fs/cgroup',p).read_text() for p in ('cpu.stat','memory.peak','io.stat')},
 'mountinfo':open('/proc/self/mountinfo').read()}))
"""


def run(mode):
    if bench.capture(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=rayforce-duckdb-journal",
        ],
        text=True,
    ).strip():
        raise RuntimeError(
            "journal project is active; preserve it before the lock check"
        )
    root = bench.RESULTS / "lock-capability" / uuid.uuid4().hex
    state = root / "state"
    state.mkdir(parents=True)
    ingestion.save(
        root / "environment.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    bench.ROOT / "docker-compose.journal.yml",
                    bench.ROOT / "docker-compose.journal-volume.yml",
                ]
            },
        },
    )
    volume = "rayforce-journal-lock-" + root.name
    env = dict(
        os.environ,
        JOURNAL_STORAGE_PATH=str(state),
        JOURNAL_VOLUME=volume,
        LAB_UID=str(os.getuid()),
        LAB_GID=str(os.getgid()),
    )
    compose = [
        "docker",
        "compose",
        "-f",
        str(bench.ROOT / "docker-compose.journal.yml"),
    ]
    if mode == "volume":
        compose += ["-f", str(bench.ROOT / "docker-compose.journal-volume.yml")]
    compose += ["--profile", "tools"]
    name = "rayforce-journal-lock-owner-" + root.name[:8]
    launched = False
    result = None
    try:
        if mode == "volume":
            subprocess.run(
                compose + ["create", "init-volume"], env=env, check=True, timeout=30
            )
            info = json.loads(
                bench.capture(["docker", "volume", "inspect", volume], text=True)
            )[0]
            if (
                info["Labels"].get("com.docker.compose.project")
                != "rayforce-duckdb-journal"
                or info["Labels"].get("com.docker.compose.volume") != "journal-data"
            ):
                raise RuntimeError("initialization volume is not owned")
            init = inspect_owned("rayforce-duckdb-journal-init-volume-1")
            if not any(
                m["Type"] == "volume"
                and m["Name"] == volume
                and m["Destination"] == "/state"
                for m in init["Mounts"]
            ):
                raise RuntimeError("initializer mount differs from owned volume")
            subprocess.run(
                ["docker", "start", "--attach", init["Id"]], check=True, timeout=30
            )
            if inspect_owned(init["Id"])["State"]["ExitCode"]:
                raise RuntimeError("volume initialization failed")
            ingestion.save(root / "volume.json", info)
        subprocess.run(
            compose
            + ["run", "-d", "--no-deps", "--name", name, "contender", "-c", HOLDER],
            env=env,
            check=True,
            timeout=30,
        )
        launched = True
        deadline = time.monotonic() + 10
        while True:
            raw = bench.capture(["docker", "logs", name], text=True)
            if raw.strip():
                owner = json.loads(raw)
                break
            if time.monotonic() > deadline:
                raise TimeoutError("lock holder failed to start")
            time.sleep(0.1)
        if not owner["ready"] or not inspect_owned(name)["State"]["Running"]:
            raise RuntimeError("lock holder is not live")
        ingestion.save(root / "owner-inspection.json", inspect_owned(name))
        same = json.loads(
            bench.capture(["docker", "exec", name, "python", "-c", TRY], text=True)
        )
        cross = json.loads(
            subprocess.check_output(
                compose + ["run", "--rm", "-T", "--no-deps", "contender", "-c", TRY],
                env=env,
                text=True,
                timeout=15,
            )
        )
        if not inspect_owned(name)["State"]["Running"]:
            raise RuntimeError("lock holder exited during the probes")
        result = {
            "mode": mode,
            "owner": owner,
            "same_container": same,
            "cross_container": cross,
            "locks_enforced": same["blocked"] and cross["blocked"],
            "scope": "Python standard fcntl.flock capability, not native database correctness or performance. Same inode/device are recorded. Empty lock file: zero payload/allocated bytes; data compression not applicable. Cgroup/process CPU/memory and block I/O recorded; physical host I/O unavailable.",
        }
        ingestion.save(root / "result.json", result)
    finally:
        if launched:
            info = inspect_owned(name)
            if info["State"]["Running"]:
                subprocess.run(["docker", "kill", name], check=True, timeout=30)
            subprocess.run(["docker", "rm", name], check=True, timeout=30)
        subprocess.run(
            compose + ["down", "--volumes", "--remove-orphans"],
            env=env,
            check=True,
            timeout=45,
        )
    result["finalized_after_cleanup"] = True
    ingestion.save(root / "result.json", result)
    print(mode, "locks enforced:", result["locks_enforced"], root, flush=True)
    return result["locks_enforced"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("bind", "volume"))
    args = parser.parse_args()
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit(0 if run(args.mode) else 1)
