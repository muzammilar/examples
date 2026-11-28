"""Local filesystem lifecycle and separate, measured dependency outages."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import lab

STATE = lab.ROOT / "results/fixture.json"


def settings(path):
    native = lab.compose(
        "exec",
        "-T",
        "metadata",
        "psql",
        "-U",
        "juicefs",
        "-d",
        "juicefs",
        "-Atc",
        "SELECT version(),current_setting('fsync'),current_setting('synchronous_commit'),current_setting('full_page_writes');",
    )
    (path / "postgres-settings.log").write_text(native)
    if not native.strip().endswith("|on|on|on"):
        raise ValueError("Metadata durability changed")
    return native


def storage(path):
    mounts = []
    for name in lab.output(
        "docker",
        "volume",
        "ls",
        "--filter",
        "label=com.docker.compose.project=" + lab.PROJECT,
        "--quiet",
    ).split():
        volume = json.loads(lab.output("docker", "volume", "inspect", name))[0]
        if volume["Labels"].get("com.docker.compose.project") != lab.PROJECT:
            raise ValueError("Unowned volume")
        mounts += [
            "--mount",
            f"type=volume,source={name},target=/storage/{name},readonly",
        ]
    raw = lab.output(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--user",
        "0",
        "--cpus",
        ".1",
        "--memory",
        "128m",
        "--label",
        "com.docker.compose.project=" + lab.PROJECT,
        "--label",
        "com.docker.compose.service=volume-inventory",
        *mounts,
        "--mount",
        "type=bind,source="
        + str(lab.ROOT / "scripts/volume_usage.py")
        + ",target=/app/volume_usage.py,readonly",
        "--entrypoint",
        "python3",
        "examples/juicefs-dependency-recovery:1.4.2",
        "/app/volume_usage.py",
    )
    (path / ("volumes-" + str(time.time_ns()) + ".json")).write_text(raw)


def mount_ready(role):
    return "fuse.juicefs" in lab.compose(
        "exec",
        "-T",
        role,
        "findmnt",
        "-rn",
        "-o",
        "FSTYPE",
        "-T",
        "/mnt/lab",
        timeout=10,
    )


def mounts(path, roles=("files-writer", "files-reader")):
    observed = {}
    for role in roles:
        lab.wait(lambda: mount_ready(role))
        observed[role] = json.loads(
            lab.compose(
                "exec",
                "-T",
                role,
                "python3",
                "-c",
                "import os,json;print(json.dumps({'device':os.stat('/mnt/lab').st_dev,'config':open('/mnt/lab/.config').read()}))",
            )
        )
    if len({r["device"] for r in observed.values()}) != len(roles):
        raise ValueError("Expected distinct FUSE superblocks")
    lab.save(path / ("mounts-" + str(time.time_ns()) + ".json"), observed)


def verify(path, fixture):
    mounts(path)
    for run in fixture["runs"]:
        for role in ("files-reader", "files-writer"):
            lab.Workload(path, role, "verify", run).finish()
    file, tree = lab.dump(path)
    if tree["Setting"]["UUID"] != fixture["uuid"]:
        raise ValueError("Filesystem identity changed")
    log = path / ("native-audit-" + str(time.time_ns()) + ".log")
    with log.open("x") as out:
        subprocess.run(
            [
                *lab.COMPOSE,
                "run",
                "--rm",
                "--no-deps",
                "control",
                "/opt/lab/audit.py",
                "/results/" + str(file.relative_to(lab.ROOT / "results")),
            ],
            cwd=lab.ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=240,
        )
    verdict = next(r for r in lab.records(log) if "native_replica_reads" in r)
    lab.save(path / "verification.json", verdict)
    settings(path)
    storage(path)
    return verdict


def start_actor(path, fixture, mode):
    run = uuid.uuid4().hex
    reader_id = uuid.uuid4().hex
    baseline = fixture["runs"][0]
    reader = lab.Workload(path, "files-reader", "reader", baseline, reader_id)
    writer = None
    try:
        lab.wait(lambda: reader.progress("read_cycle") > 0, 60)
        writer = lab.Workload(
            path,
            "files-writer",
            "writer",
            run,
            str(fixture["live_files"]),
            str(fixture["payload_bytes"]),
        )
        fixture["runs"].append(run)
        lab.save(STATE, fixture)
        lab.wait(lambda: writer.progress() >= 10, 60)
        return run, reader_id, reader, writer
    except BaseException:
        (lab.ROOT / "results" / ("stop-reader-" + reader_id)).touch()
        if writer:
            writer.finish()
        reader.finish()
        raise


def discover_survivors(excluded_id):
    survivors = {}
    for identifier in lab.output(
        "docker",
        "ps",
        "--no-trunc",
        "--filter",
        "label=com.docker.compose.project=" + lab.PROJECT,
        "--quiet",
    ).split():
        row = lab.owned(identifier)
        if row["Id"] != excluded_id:
            survivors[row["Id"]] = row["State"]["StartedAt"]
    return survivors


def recovered_cold_read(path, baseline):
    def probe():
        rows = lab.Workload(path, "cold-reader", "verify", baseline).finish(
            allowed_error=True
        )
        if any(r.get("event") == "verified" for r in rows):
            return rows
        failures = [r for r in rows if r.get("event") == "fatal"]
        allowed = (
            "Input/output error",
            "Connection timed out",
            "Connection reset",
            "Connection refused",
            "Resource temporarily unavailable",
        )
        if not failures or not any(text in failures[0]["error"] for text in allowed):
            raise AssertionError("Wrong/unexplained cold result during recovery")
        return None

    return lab.wait(probe, 90)


def outage(path, fixture, role):
    if (fixture["files"] + len(fixture["runs"]) * fixture["live_files"]) * fixture[
        "payload_bytes"
    ] > 536870912:
        raise ValueError(
            "Another live case exceeds the bounded 512MiB fixture; preserve/retire it first"
        )
    verify(path, fixture)
    if role == "s3":
        lab.compose(
            "--profile", "tools", "up", "--detach", "--force-recreate", "cold-reader"
        )
        mounts(path, ("files-writer", "files-reader", "cold-reader"))
    run, reader_id, reader, writer = start_actor(path, fixture, role)
    identifier = lab.compose("ps", "--quiet", role).strip()
    before = lab.owned(identifier, role)
    cold = None
    faulted = False
    try:
        others = discover_survivors(before["Id"])
        lab.save(path / "survivor-started-at.json", others)
        ack_before = writer.progress()
        lab.save(path / "fault-before.json", before)
        lab.output("docker", "kill", "--signal", "KILL", identifier)
        faulted = True
        dead = lab.owned(identifier, role)
        lab.save(path / "fault-killed.json", dead)
        if (
            dead["State"]["Running"]
            or dead["State"]["ExitCode"] != 137
            or dead["State"]["OOMKilled"]
        ):
            raise ValueError("Dependency crash not proven")
        if role == "s3":
            cold = lab.Workload(path, "cold-reader", "verify", fixture["runs"][0])
        time.sleep(20 if role == "s3" else 15)
        writer.alive()
        reader.alive()
        ack_down = writer.progress()
        if cold and cold.process.poll() == 0:
            raise ValueError(
                "Cold corpus served before object-store recovery; fault/cache evidence is insufficient"
            )
        lab.owned(identifier, role)
        lab.output("docker", "start", identifier)
        faulted = False
        lab.wait(
            lambda: lab.owned(identifier, role)["State"].get("Health", {}).get("Status")
            == "healthy",
            90,
        )
        restored = lab.owned(identifier, role)
        lab.save(path / "fault-restored.json", restored)
        if restored["State"]["StartedAt"] == before["State"]["StartedAt"]:
            raise ValueError("Dependency restart not proven")
        if cold:
            rows = cold.finish(allowed_error=True)
            if not any(r.get("event") == "mount_verified" for r in rows) or not any(
                r.get("event") in ("fatal", "verified") for r in rows
            ):
                raise ValueError("Cold read did not execute a qualified workload")
            failures = [r for r in rows if r.get("event") == "fatal"]
            if failures and not any(
                text in failures[0]["error"]
                for text in (
                    "Input/output error",
                    "Connection timed out",
                    "Connection reset",
                    "Connection refused",
                    "Resource temporarily unavailable",
                )
            ):
                raise ValueError("Cold reader had an unexpected/wrong-body failure")
            lab.save(
                path / "cold-probe.json", dict(rows=rows, joined_after_restore=True)
            )
            cold = None
            recovered_cold_read(path, fixture["runs"][0])
            writer.alive()
        writer_rows = writer.finish()
        writer = None
        if sum(r.get("event") == "ack" for r in writer_rows) <= ack_down:
            raise ValueError("No post-recovery source ACK progress")
        (lab.ROOT / "results" / ("stop-reader-" + reader_id)).touch()
        reader.finish()
        reader = None
        for ident, started in others.items():
            now = lab.owned(ident)
            if not now["State"]["Running"] or now["State"]["StartedAt"] != started:
                raise ValueError("Unexpected survivor restart")
        result = verify(path, fixture)
        fixture.setdefault("completed_faults", []).append(role)
        lab.save(STATE, fixture)
        lab.save(
            path / "recovery.json",
            dict(
                passed=True,
                role=role,
                run=run,
                acks_before=ack_before,
                acks_before_restore=ack_down,
                final=result,
            ),
        )
    finally:
        if faulted:
            lab.owned(identifier, role)
            lab.output("docker", "start", identifier)
        (lab.ROOT / "results" / ("stop-reader-" + reader_id)).touch()
        if writer:
            writer.finish()
        if reader:
            reader.finish()
        if cold:
            cold.finish(allowed_error=True)


def main(mode, path):
    if mode == "docker-up":
        cfg = json.loads(
            lab.compose("--profile", "tools", "config", "--format", "json")
        )
        info = json.loads(lab.output("docker", "info", "--format", "{{json .}}"))
        caps = sum(float(s["cpus"]) for s in cfg["services"].values())
        memory = sum(int(s["mem_limit"]) for s in cfg["services"].values())
        if (
            info["Architecture"] != "aarch64"
            or caps > info["NCPU"]
            or memory + 2 * 1024**3 > info["MemTotal"]
        ):
            raise ValueError("Pinned ARM64 lab/all-profile budget does not fit Docker")
        values = dict(
            id=uuid.uuid4().hex,
            uuid=None,
            runs=[],
            files=int(os.environ.get("FILES", "128")),
            live_files=int(os.environ.get("LIVE_FILES", "512")),
            payload_bytes=int(os.environ.get("PAYLOAD_BYTES", "262144")),
            cpus=caps,
            memory=memory,
            kernel=info["KernelVersion"],
            architecture=info["Architecture"],
            docker_cpus=info["NCPU"],
            docker_memory=info["MemTotal"],
            local_only=True,
        )
        if (
            not (
                1 <= values["files"] <= 1024
                and 1 <= values["live_files"] <= 1024
                and 1 <= values["payload_bytes"] <= 1048576
            )
            or max(values["files"], values["live_files"]) * values["payload_bytes"]
            > 268435456
            or (values["files"] + 2 * values["live_files"]) * values["payload_bytes"]
            > 536870912
        ):
            raise ValueError("Corpus exceeds bounded level-3 storage budget")
        if STATE.exists():
            old = json.loads(STATE.read_text())
            if any(
                old[k] != values[k]
                for k in ("files", "live_files", "payload_bytes", "cpus", "memory")
            ):
                raise ValueError("Refusing changed corpus/budget for retained state")
            values = old
        elif lab.compose("ps", "--all", "--quiet").strip():
            raise ValueError("Containers without owned fixture")
        lab.save(STATE, values)
        with (path / "build.log").open("x") as stream:
            subprocess.run(
                [*lab.COMPOSE, "--profile", "tools", "build", "files-writer"],
                cwd=lab.ROOT,
                check=True,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )
        free = int(
            lab.output(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--cpus",
                ".1",
                "--memory",
                "64m",
                "--entrypoint",
                "python3",
                "examples/juicefs-dependency-recovery:1.4.2",
                "-c",
                "import os;s=os.statvfs('/');print(s.f_bavail*s.f_frsize)",
            )
        )
        if free < 5 * 1024**3:
            raise ValueError("Less than 5GiB Docker headroom; refusing storage startup")
        lab.save(
            path / "preflight.json",
            dict(
                free_bytes=free,
                all_profile_cpu_caps=caps,
                all_profile_memory_caps=memory,
            ),
        )
        lab.compose(
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "180",
            *lab.STORAGE,
            timeout=240,
        )
        settings(path)
        storage(path)
        lab.save(
            path / "images.json",
            [
                dict(
                    role=lab.owned(i)["Config"]["Labels"]["com.docker.compose.service"],
                    image=lab.owned(i)["Image"],
                )
                for i in lab.compose("ps", "--quiet").split()
            ],
        )
        return
    fixture = json.loads(STATE.read_text())
    if mode == "up":
        if fixture["uuid"] is None:
            lab.admin(
                path,
                "format",
                "--storage=s3",
                "--bucket=http://s3:8333/juicefs",
                "--access-key=juicefs-lab",
                "--secret-key=juicefs-local-only-secret",
                "--compress=none",
                "--trash-days=1",
                lab.META,
                "juicefs-lab",
            )
            _, tree = lab.dump(path)
            fixture["uuid"] = tree["Setting"]["UUID"]
            lab.save(STATE, fixture)
        lab.compose("up", "--detach", "files-writer", "files-reader")
        mounts(path)
        if fixture["runs"]:
            verify(path, fixture)
        return
    if mode == "test":
        if not fixture["runs"]:
            run = uuid.uuid4().hex
            fixture["runs"].append(run)
            lab.save(STATE, fixture)
            lab.Workload(
                path,
                "files-writer",
                "seed",
                run,
                str(fixture["files"]),
                str(fixture["payload_bytes"]),
            ).finish()
        verify(path, fixture)
        return
    if mode == "verify":
        verify(path, fixture)
        return
    if mode == "backup":
        verify(path, fixture)
        from backup import preserve

        preserve(path, fixture)
        return
    if mode == "locks":
        run = uuid.uuid4().hex
        holder = lab.Workload(path, "files-writer", "lock-hold", run)
        try:
            lab.wait(lambda: holder.progress("lock_held") > 0, 30)
            lab.Workload(path, "files-reader", "lock-try", run).finish(30)
        finally:
            holder.finish(30)
        lab.save(path / "locks.json", dict(passed=True, run=run))
        return
    if mode in ("metadata-outage", "object-store-outage"):
        outage(path, fixture, "metadata" if mode == "metadata-outage" else "s3")
        return
    if mode == "down":
        verify(path, fixture)
        lab.compose(
            "stop",
            "--timeout",
            "30",
            "files-writer",
            "files-reader",
            "cold-reader",
            timeout=120,
        )
        # All witnesses are already on an example-owned bind mount outside dependency storage.
        snapshots = {
            str(f.relative_to(lab.ROOT / "results")): hashlib.sha256(
                f.read_bytes()
            ).hexdigest()
            for f in (lab.ROOT / "results/journals").glob("*.jsonl")
        }
        lab.save(path / "witness-sha256.json", snapshots)
        lab.compose("stop", "--timeout", "30", *lab.STORAGE, timeout=180)
        stopped = []
        for identifier in lab.compose("ps", "--all", "--quiet").split():
            row = lab.owned(identifier)
            stopped.append(
                dict(
                    role=row["Config"]["Labels"]["com.docker.compose.service"],
                    running=row["State"]["Running"],
                    exit_code=row["State"]["ExitCode"],
                    oom=row["State"]["OOMKilled"],
                    finished_at=row["State"]["FinishedAt"],
                )
            )
        lab.save(path / "stopped-state.json", stopped)
        if any(
            row["running"] or row["exit_code"] != 0 or row["oom"] for row in stopped
        ):
            raise ValueError(
                "Owned shutdown was not clean; retain state and inspect exit receipts"
            )
        fixture["fenced_and_verified"] = str(path)
        lab.save(STATE, fixture)
        return
    if mode == "docker-down":
        if not fixture.get("fenced_and_verified"):
            raise ValueError("Run down to reconcile and fence first")
        lab.compose(
            "--profile",
            "tools",
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "30",
            timeout=180,
        )
        remnants = {
            kind: lab.output(
                "docker",
                kind,
                "ls",
                "--filter",
                "label=com.docker.compose.project=" + lab.PROJECT,
                "--quiet",
            ).split()
            for kind in ("container", "network", "volume")
        }
        lab.save(path / "remnants.json", remnants)
        if any(remnants.values()):
            raise ValueError("Owned resources remain")
        lab.save(path / "retired-fixture.json", fixture)
        STATE.unlink()
        return
    raise ValueError("Unknown action")


if __name__ == "__main__":
    path = Path(os.environ["JUICEFS_PHASE_DIR"]).resolve()
    path.relative_to(lab.ROOT / "results")
    with (lab.ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = dict(name=sys.argv[1], passed=False, started_ns=time.time_ns())
        lab.save(path / "status.json", status)
        sources = {
            str(f.relative_to(lab.ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in lab.ROOT.rglob("*")
            if f.is_file()
            and not any(
                part in ("results", ".build", "__pycache__", "reports")
                for part in f.relative_to(lab.ROOT).parts
            )
        }
        lab.save(path / "source-sha256.json", sources)
        if STATE.exists():
            lab.save(path / "fixture.json", json.loads(STATE.read_text()))
        try:
            main(sys.argv[1], path)
            status["passed"] = True
        except BaseException as error:
            status["error"] = str(error)
            (path / "native-error.log").write_text(
                lab.compose("logs", "--no-color", "--tail", "100", timeout=45)
            )
            raise
        finally:
            status["ended_ns"] = time.time_ns()
            lab.save(path / "status.json", status)
