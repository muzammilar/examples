"""Owned Linux-volume setup, disk observations and fenced evidence export."""

import json
import subprocess
import time

import experiment as bench
import ingestion
from journal import inspect_owned


SAMPLE = r"""
import json,pathlib
root=pathlib.Path('/state'); files={}; raced=[]
for path in root.rglob('*'):
    if path.is_symlink(): raise RuntimeError('unexpected symlink in native state')
    try:
        stat=path.stat()
        if path.is_file(): files[str(path.relative_to(root))]={'bytes':stat.st_size,'allocated_bytes':stat.st_blocks*512}
    except FileNotFoundError: raced.append(str(path.relative_to(root)))
print(json.dumps({'files':files,'raced_paths':raced,'logical_bytes':sum(v['bytes'] for v in files.values()),'allocated_bytes':sum(v['allocated_bytes'] for v in files.values())}))
"""


def initialize(compose, env, root):
    subprocess.run(compose + ["create", "init-volume"], env=env, check=True, timeout=30)
    volume = json.loads(
        bench.capture(["docker", "volume", "inspect", env["JOURNAL_VOLUME"]], text=True)
    )[0]
    if (
        volume["Labels"].get("com.docker.compose.project") != "rayforce-duckdb-journal"
        or volume["Labels"].get("com.docker.compose.volume") != "journal-data"
    ):
        raise RuntimeError("initialization volume ownership differs")
    info = inspect_owned("rayforce-duckdb-journal-init-volume-1")
    if not any(
        m["Type"] == "volume"
        and m["Name"] == env["JOURNAL_VOLUME"]
        and m["Destination"] == "/state"
        for m in info["Mounts"]
    ):
        raise RuntimeError("initialization mount differs from the owned volume")
    log = subprocess.check_output(
        ["docker", "start", "--attach", info["Id"]], text=True, timeout=30
    )
    completed = inspect_owned(info["Id"])
    ingestion.save(
        root / "volume-initialization.json",
        {"volume": volume, "inspection": completed, "log": log},
    )
    if completed["State"]["Running"] or completed["State"]["ExitCode"]:
        raise RuntimeError("volume initialization failed")
    subprocess.run(["docker", "rm", info["Id"]], check=True, timeout=30)


def sample(server):
    value = json.loads(
        bench.capture(["docker", "exec", server, "python", "-c", SAMPLE], text=True)
    )
    value["monotonic_ns"] = time.monotonic_ns()
    value["scope"] = (
        "Actual /state volume observed by a Python process in the server cgroup; its CPU/memory overhead is included. Directory traversal is non-atomic."
    )
    return value


def export(server, destination, root):
    info = inspect_owned(server)
    if info["State"]["Running"]:
        subprocess.run(
            ["docker", "kill", "--signal", "KILL", info["Id"]], check=True, timeout=30
        )
    stopped = inspect_owned(server)
    ingestion.save(root / "volume-export-fenced.json", stopped)
    if stopped["State"]["Running"]:
        raise RuntimeError("native state owner was not fenced before export")
    subprocess.run(
        ["docker", "cp", info["Id"] + ":/state/.", str(destination)],
        check=True,
        timeout=60,
    )
    value = bench.storage_sample(destination)
    ingestion.save(root / "volume-export.json", value)
    return value
