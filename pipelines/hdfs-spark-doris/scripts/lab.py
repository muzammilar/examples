"""Example-owned commands, immutable phase logs and bounded resource sampling."""

import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "hdfs-spark-doris"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
ROLES = [
    *[f"fe{i}" for i in range(1, 4)],
    *[f"be{i}" for i in range(1, 4)],
    "namenode",
    "dn1",
    "dn2",
    "dn3",
    "master",
    "worker1",
    "worker2",
]


def output(*args, timeout=900):
    return subprocess.check_output(
        list(map(str, args)),
        cwd=ROOT,
        text=True,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def compose(*args, timeout=900):
    return output(*COMPOSE, *args, timeout=timeout)


def save(path, value):
    with path.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def phase(name):
    path = ROOT / "results" / (name + "-" + str(time.time_ns()))
    path.mkdir(parents=True)
    save(
        path / "status.json",
        {"passed": False, "name": name, "started_ns": time.time_ns()},
    )
    fixture = ROOT / "results/fixture.json"
    if fixture.exists():
        save(path / "fixture.json", json.loads(fixture.read_text()))
    return path


def owned(identifier, role=None):
    row = json.loads(output("docker", "inspect", identifier))[0]
    labels = row["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != PROJECT or (
        role and labels.get("com.docker.compose.service") != role
    ):
        raise ValueError("Refusing unowned container")
    return row


def counters(identifier):
    row = owned(identifier)
    command = (
        ["/oracle", "cgroup"]
        if row["Config"]["Labels"].get("com.docker.compose.service") == "oracle"
        else [
            "sh",
            "-c",
            "for f in cpu.stat memory.current memory.peak memory.events io.stat; do echo METRIC:$f; cat /sys/fs/cgroup/$f; done",
        ]
    )
    result = subprocess.run(
        ["docker", "exec", identifier, *command],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return dict(
        role=row["Config"]["Labels"]["com.docker.compose.service"],
        started=row["State"]["StartedAt"],
        at_ns=time.time_ns(),
        output=result.stdout,
        error=result.stderr,
        status=result.returncode,
    )


def snapshot(path, tag):
    ids = compose("ps", "--all", "--quiet").split()
    active = [i for i in ids if owned(i)["State"]["Running"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        save(
            path / (tag + "-cgroups.json"),
            dict(zip(active, pool.map(counters, active))),
        )
    if active:
        save(path / (tag + "-inspect.json"), [owned(i) for i in active])


class Observer:
    def __init__(self, path):
        self.path, self.stop = path, threading.Event()

    def collect(self):
        with (self.path / "resources.jsonl").open("a") as log:
            while not self.stop.is_set():
                try:
                    ids = compose("ps", "--all", "--quiet").split()
                    active = [i for i in ids if owned(i)["State"]["Running"]]
                    if active:
                        raw = output(
                            "docker",
                            "stats",
                            "--no-stream",
                            "--format",
                            "{{json .}}",
                            *active,
                            timeout=30,
                        )
                        log.write(
                            json.dumps(
                                dict(
                                    utc=datetime.datetime.now(
                                        datetime.timezone.utc
                                    ).isoformat(),
                                    stats=[
                                        json.loads(line) for line in raw.splitlines()
                                    ],
                                )
                            )
                            + "\n"
                        )
                        log.flush()
                except (subprocess.SubprocessError, ValueError, KeyError) as error:
                    log.write(json.dumps({"collector_error": str(error)}) + "\n")
                    log.flush()
                self.stop.wait(3)

    def __enter__(self):
        snapshot(self.path, "before")
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=35)
        snapshot(self.path, "after")


def actor(path, role, args, *, extra=(), timeout=900):
    name = PROJECT + "-" + role + "-" + str(time.time_ns())
    with (path / (name + ".log")).open("x") as log:
        try:
            result = subprocess.run(
                [*COMPOSE, "run", "--name", name, "--use-aliases", *extra, role, *args],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            owned(name, role)
            output("docker", "stop", "--time", "20", name, timeout=45)
            save(path / (name + "-timed-out-inspect.json"), owned(name, role))
            raise
    row = owned(name, role)
    # Actor prints counters in finally before exit; inspect retains exit status/limits.
    save(path / (name + "-inspect.json"), row)
    if result.returncode or row["State"]["ExitCode"]:
        raise RuntimeError("Actor failed; retained log/container: " + name)
    output("docker", "rm", name)
    return path / (name + ".log")


def hdfs(*args):
    return compose("exec", "-T", "namenode", "hdfs", *args, timeout=60)


def records(path):
    """Ignore Compose diagnostics; fail on malformed structured workload output."""
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.startswith("{")
    ]
