"""Owned commands, immutable logs and exact workload observations for this lab."""

import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "juicefs-dependency-recovery"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
META = "postgres://juicefs@metadata:5432/juicefs?sslmode=disable&connect_timeout=5&max_open_conns=8&max_idle_conns=4"
STORAGE = [
    *[f"{r}{i}" for r in ("master", "etcd", "volume") for i in range(1, 4)],
    "filer1",
    "filer2",
    "s3",
    "metadata",
]


def output(*args, timeout=180):
    return subprocess.check_output(
        list(map(str, args)),
        cwd=ROOT,
        text=True,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def compose(*args, timeout=180):
    return output(*COMPOSE, *args, timeout=timeout)


def save(path, value):
    with path.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def owned(identifier, role=None):
    row = json.loads(output("docker", "inspect", identifier))[0]
    labels = row["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != PROJECT or (
        role and labels.get("com.docker.compose.service") != role
    ):
        raise ValueError("Refusing unowned container")
    return row


def wait(probe, timeout=120):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (subprocess.SubprocessError, ValueError, RuntimeError) as error:
            last = str(error)
        time.sleep(0.5)
    raise RuntimeError("Probe timed out: " + str(last))


def records(path, strict=True):
    rows = []
    for line in path.read_text().splitlines(keepends=True):
        if line.startswith("{") and line.endswith("\n"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if strict:
                    raise
    return rows


class Workload:
    def __init__(self, path, role, *args):
        self.role = role
        self.logpath = path / (role + "-" + str(time.time_ns()) + ".log")
        self.log = self.logpath.open("x")
        self.process = subprocess.Popen(
            [*COMPOSE, "exec", "-T", role, "files-workload", *map(str, args)],
            cwd=ROOT,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.finished = False

    def finish(self, timeout=180, allowed_error=False):
        if self.finished:
            return records(self.logpath)
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # The controller must restore dependencies before retrying/fencing a stuck workload.
            raise RuntimeError(
                "Workload deadline exceeded; preserve owned mount and state: "
                + str(self.logpath)
            )
        finally:
            self.log.flush()
        self.log.close()
        self.finished = True
        rows = records(self.logpath)
        if code and not allowed_error:
            raise RuntimeError("Workload failed: " + str(self.logpath))
        return rows

    def progress(self, event="ack"):
        return sum(r.get("event") == event for r in records(self.logpath))

    def alive(self):
        if self.process.poll() is not None:
            raise RuntimeError("Actor exited before recovery: " + str(self.logpath))


def admin(path, *args):
    log = path / ("admin-" + str(time.time_ns()) + ".log")
    with log.open("x") as stream:
        subprocess.run(
            [
                *COMPOSE,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "/usr/bin/python3",
                "control",
                "/opt/lab/native_tool.py",
                "juicefs",
                *map(str, args),
            ],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
    return log


def dump(path):
    target = path / ("metadata-" + str(time.time_ns()) + ".json")
    admin(path, "dump", META, "/results/" + str(target.relative_to(ROOT / "results")))
    result = json.loads(target.read_text())
    settings = result["Setting"]
    if (
        settings["Name"] != "juicefs-lab"
        or settings["Storage"] != "s3"
        or settings["Bucket"] != "http://s3:8333/juicefs"
        or settings["Compression"] != "none"
    ):
        raise ValueError("Unexpected metadata/storage contract")
    return target, result
