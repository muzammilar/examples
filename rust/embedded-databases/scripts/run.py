"""Serialize supported host lifecycle commands; never assume host/VM flock equivalence."""

import contextlib
import fcntl
import json
import os
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


@contextlib.contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("controller lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another example lifecycle command is active") from error
        yield os.fstat(descriptor)
    finally:
        os.close(descriptor)


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def command(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def action(name):
    compose = ["docker", "compose"]
    run = compose + ["run", "--rm", "evaluation"]
    if name == "docker-up":
        command(*compose, "build", "evaluation")
        command(str(ROOT / "environment.sh"))
        command(
            *compose,
            "run",
            "--rm",
            "--user",
            "0",
            "--entrypoint",
            "/bin/sh",
            "evaluation",
            "-ec",
            f"chown {os.getuid()}:{os.getgid()} /database",
        )
    elif name == "up":
        command(*run, "prepare")
    elif name in {"test", "test-production", "crash-test"}:
        command(*run, "test")
    elif name == "down":
        command(*run, "verify")
        command(*compose, "stop")
    elif name == "docker-down":
        command(str(ROOT / "cleanup.sh"))
    elif name == "report":
        command(*run, "report")
        temporary = RESULTS / ("published-" + uuid.uuid4().hex + ".json")
        shutil.copyfile(RESULTS / "summary.json", temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, ROOT / "config/RESULTS.json")
    elif name == "status":
        command(*compose, "ps", "--all")
        if (RESULTS / "lab.json").exists():
            print((RESULTS / "lab.json").read_text(), end="")
    elif name == "cli":
        command(*compose, "run", "--rm", "--entrypoint", "/bin/sh", "evaluation")
    else:
        raise ValueError("unknown lifecycle action: " + name)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: run.py docker-up|up|test|down|docker-down|report|status|cli"
        )
    RESULTS.mkdir(exist_ok=True)
    with lock(RESULTS / "controller.lock") as identity:
        directory = RESULTS / "host-controllers" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started, cpu = time.monotonic(), time.process_time()
        outcome = {
            "action": sys.argv[1],
            "started_ns": time.time_ns(),
            "lock_inode": identity.st_ino,
            "lock_device": identity.st_dev,
        }
        save(directory / "started.json", outcome)
        try:
            action(sys.argv[1])
        except BaseException as error:
            outcome.update(passed=False, error=repr(error))
            raise
        else:
            outcome["passed"] = True
        finally:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            outcome.update(
                ended_ns=time.time_ns(),
                wall_seconds=time.monotonic() - started,
                host_controller_cpu_core_seconds=time.process_time() - cpu,
                child_cli_cpu_core_seconds=after.ru_utime
                + after.ru_stime
                - before.ru_utime
                - before.ru_stime,
                host_controller_peak_rss_bytes=resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss
                * (1 if sys.platform == "darwin" else 1024),
                child_cli_lifetime_peak_rss_bytes=after.ru_maxrss
                * (1 if sys.platform == "darwin" else 1024),
                scope="Host lifecycle wrapper and direct CLI children only; Docker daemon/build/database costs are separate, not included in these CPU counters.",
            )
            save(directory / "completed.json", outcome)


if __name__ == "__main__":
    main()
