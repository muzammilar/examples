"""Native Quack bootstrap/authentication gate; live recovery uses this owned stack."""

import fcntl
import hashlib
import json
import os
import resource
import re
import subprocess
import time
import uuid

import experiment as bench
import ingestion
from journal import NativeIPC
from journal_restore import archive_compressibility, fingerprint
from journal_volume import SAMPLE

PROJECT = "duckdb-live-recovery"
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(bench.ROOT / "docker-compose.duckdb-live.yml"),
    "--profile",
    "tools",
]
NATIVE = r"""
import json,pathlib,os,time
root=pathlib.Path('/proc/1')
print(json.dumps({'observed_unix_ns':time.time_ns(),'process_status':(root/'status').read_text(),
    'mountinfo':(root/'mountinfo').read_text(),
    'process_stat':(root/'stat').read_text(),'ticks_per_second':os.sysconf('SC_CLK_TCK'),
    'cgroup':{p:pathlib.Path('/sys/fs/cgroup',p).read_text()
              for p in ('cpu.stat','memory.peak','memory.events','io.stat')}}))
"""


def call(env, *arguments, timeout=60):
    return subprocess.check_output(
        COMPOSE + list(arguments), env=env, text=True, timeout=timeout
    )


def inspect(container):
    info = json.loads(bench.capture(["docker", "inspect", container], text=True))[0]
    if info["Config"]["Labels"].get("com.docker.compose.project") != PROJECT:
        raise RuntimeError("container is outside the owned DuckDB project")
    return info


def require_empty():
    for kind, flags in (
        ("ps", ["-aq"]),
        ("network", ["ls", "-q"]),
        ("volume", ["ls", "-q"]),
    ):
        if bench.capture(
            [
                "docker",
                kind,
                *flags,
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
            ],
            text=True,
        ).strip():
            raise RuntimeError("owned DuckDB resources already exist: " + kind)


def initialize(root, env):
    call(env, "create", "init-volume")
    volume = json.loads(
        bench.capture(["docker", "volume", "inspect", env["DUCKDB_VOLUME"]], text=True)
    )[0]
    if (
        volume["Labels"].get("com.docker.compose.project") != PROJECT
        or volume["Labels"].get("com.docker.compose.volume") != "database-state"
    ):
        raise RuntimeError("volume initialization ownership differs")
    identifier = call(env, "ps", "-aq", "init-volume").strip()
    info = inspect(identifier)
    if not any(
        m["Type"] == "volume"
        and m["Name"] == env["DUCKDB_VOLUME"]
        and m["Destination"] == "/state"
        for m in info["Mounts"]
    ):
        raise RuntimeError("initialization targets another storage volume")
    log = bench.capture(["docker", "start", "--attach", identifier], text=True)
    done = inspect(identifier)
    ingestion.save(
        root / "initialization.json", {"volume": volume, "inspection": done, "log": log}
    )
    if done["State"]["Running"] or done["State"]["ExitCode"]:
        raise RuntimeError("owned volume initialization failed")
    subprocess.run(["docker", "rm", identifier], check=True, timeout=30)


def start(root, env):
    call(env, "up", "-d", "--no-deps", "database")
    server = call(env, "ps", "-q", "database").strip()
    deadline = time.monotonic() + 60
    while True:
        info = inspect(server)
        log = bench.capture(
            ["docker", "logs", server], text=True, stderr=subprocess.STDOUT
        )
        matches = re.findall(r"^READY\t([0-9a-fA-F:]+)$", log, re.M)
        if matches:
            if not info["State"]["Running"]:
                raise RuntimeError("database stopped after announcing readiness")
            (root / "server.log").write_text(log)
            ingestion.save(root / "server-started.json", info)
            return server, matches[-1]
        if not info["State"]["Running"] or time.monotonic() > deadline:
            (root / "server.log").write_text(log)
            ingestion.save(root / "server-start-failed.json", info)
            raise RuntimeError("native DuckDB/Quack did not start")
        time.sleep(0.1)


def snapshot(container, root, label, disk=False):
    info = inspect(container)
    if not info["State"]["Running"]:
        raise RuntimeError("cannot observe a stopped workload")
    native = json.loads(
        bench.capture(["docker", "exec", container, "python", "-c", NATIVE], text=True)
    )
    if disk:
        native["state_disk"] = json.loads(
            bench.capture(
                ["docker", "exec", container, "python", "-c", SAMPLE], text=True
            )
        )
    after = inspect(container)
    if (
        not after["State"]["Running"]
        or after["State"]["StartedAt"] != info["State"]["StartedAt"]
    ):
        raise RuntimeError("workload generation changed during observation")
    ingestion.save(root / (label + "-inspection.json"), info)
    ingestion.save(root / (label + "-native.json"), native)
    return native


def export(server, root):
    info = inspect(server)
    if info["State"]["Running"]:
        subprocess.run(
            ["docker", "stop", "--timeout", "15", server], check=True, timeout=30
        )
    stopped = inspect(server)
    ingestion.save(root / "server-stopped.json", stopped)
    if stopped["State"]["Running"] or stopped["State"]["ExitCode"]:
        raise RuntimeError("native graceful shutdown failed; preserve storage")
    state = root / "state"
    state.mkdir()
    subprocess.run(
        ["docker", "cp", server + ":/state/.", str(state)], check=True, timeout=60
    )
    ingestion.save(root / "export.json", bench.storage_sample(state))
    return state


def capability():
    require_empty()
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    root = bench.RESULTS / "duckdb-connectivity" / uuid.uuid4().hex
    root.mkdir(parents=True)
    env = dict(
        os.environ,
        LAB_UID=str(os.getuid()),
        LAB_GID=str(os.getgid()),
        DUCKDB_VOLUME="duckdb-state-" + root.name,
    )
    tracked = [
        "duckdb_live.py",
        "docker-compose.duckdb-live.yml",
        "quack-sources.json",
        "journal.py",
        "journal_volume.py",
        "experiment.py",
        "analytics-workload/cpp/duckdb_quack.cpp",
        "analytics-workload/CMakeLists.txt",
        "docker/Dockerfile",
        "prepare.py",
    ]
    ingestion.save(
        root / "setup.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "cpu_model": bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if os.uname().sysname == "Darwin"
            else "Unresolved; supply native CPU inventory",
            "docker": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "sources": json.loads((bench.ROOT / "quack-sources.json").read_text()),
            "source_sha256": {
                name: hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest()
                for name in tracked
            },
            "probe": {"k": 1, "v": "quack-ready-1"},
        },
    )
    server = None
    exported = False
    replies, actors = {}, []
    try:
        initialize(root, env)
        server, pin = start(root, env)
        original = inspect(server)
        env["DUCKDB_FINGERPRINT"] = pin
        ingestion.save(root / "pins.json", {"fingerprint": pin})
        control_name = "duckdb-control-" + root.name[:8]
        valid = NativeIPC(COMPOSE, env, control_name, root / "control.stderr")
        actors.append(valid)
        for sql, expected in [
            ("CREATE TABLE probe(k BIGINT PRIMARY KEY, v VARCHAR NOT NULL)", ""),
            ("INSERT INTO probe VALUES (1,'quack-ready-1')", "1"),
            ("SELECT k,v FROM probe", "1\tquack-ready-1"),
        ]:
            reply = valid.query(sql)
            replies[sql] = reply
            if reply["status"] != "ok" or reply["body"] != expected:
                raise RuntimeError("native TLS SQL roundtrip differs: " + str(reply))
        snapshot(server, root, "server-before-auth-tests", disk=True)
        snapshot(control_name, root, "control-before-close")
        valid.close()
        actors.remove(valid)
        for name, changes, expected in [
            (
                "bad-token",
                {"DUCKDB_LAB_TOKEN": "incorrect_lab_token_2026"},
                "authentication",
            ),
            ("bad-fingerprint", {"DUCKDB_FINGERPRINT": "0" * 64}, "certificate"),
        ]:
            identifier = "duckdb-" + name + "-" + root.name[:8]
            actor = NativeIPC(
                COMPOSE, dict(env, **changes), identifier, root / (name + ".stderr")
            )
            actors.append(actor)
            reply = actor.query("SELECT 1")
            replies[name] = reply
            if reply["status"] != "error" or expected not in reply["body"].lower():
                raise RuntimeError(
                    "authentication/pin rejection was not verified: " + str(reply)
                )
            snapshot(identifier, root, name)
            actor.close()
            actors.remove(actor)
            current = inspect(server)
            if (
                not current["State"]["Running"]
                or current["State"]["StartedAt"] != original["State"]["StartedAt"]
            ):
                raise RuntimeError("server failed/restarted during auth rejection")
        identifier = "duckdb-post-auth-" + root.name[:8]
        actor = NativeIPC(COMPOSE, env, identifier, root / "post-auth.stderr")
        actors.append(actor)
        replies["post-auth"] = actor.query("SELECT k,v FROM probe")
        if (
            replies["post-auth"]["status"] != "ok"
            or replies["post-auth"]["body"] != "1\tquack-ready-1"
        ):
            raise RuntimeError("acknowledged probe changed after auth tests")
        snapshot(identifier, root, "post-auth")
        actor.close()
        actors.remove(actor)
        snapshot(server, root, "server-before-stop", disk=True)
        state = export(server, root)
        exported = True
        native_files = sorted(
            p
            for p in state.iterdir()
            if p.is_file() and p.name.startswith("events.duckdb")
        )
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result = {
            "passed": True,
            "finalized_after_cleanup": False,
            "scope": "Native C++/signed Quack TLS/authentication gate, not live crash recovery or a benchmark.",
            "replies": replies,
            "native_files": fingerprint(native_files),
            "archive_compressibility": archive_compressibility(native_files),
            "host_controller": {
                "wall_ns": time.monotonic_ns() - started,
                "cpu_ns": time.process_time_ns() - cpu,
                "peak_rss_bytes": usage.ru_maxrss
                * (1 if os.uname().sysname == "Darwin" else 1024),
            },
        }
        ingestion.save(root / "result.json", result)
    finally:
        errors = []
        for actor in actors:
            try:
                actor.close()
            except Exception as error:
                errors.append(str(error))
        try:
            call(env, "down", "--remove-orphans", *(["--volumes"] if exported else []))
        except Exception as error:
            errors.append(str(error))
        if errors:
            raise RuntimeError(
                "DuckDB cleanup failed; preserve evidence: " + repr(errors)
            )
    require_empty()
    result["finalized_after_cleanup"] = True
    ingestion.save(root / "result.json", result)
    print("DuckDB native TLS connectivity passed:", root, flush=True)


if __name__ == "__main__":
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        capability()
