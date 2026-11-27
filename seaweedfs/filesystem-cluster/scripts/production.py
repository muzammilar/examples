"""Separate cross-mount ownership and filer-outage cases with retained witnesses."""

import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import control as c


def rows(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_bytes().splitlines(keepends=True)
        if line.endswith(b"\n") and line.startswith(b"{")
    ]


def wait_for(predicate, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError("No expected workload progress before deadline")


def inspect(role):
    identifier = c.command("ps", "--all", "--quiet", role).strip()
    if not identifier or "\n" in identifier:
        raise RuntimeError("Expected one owned container")
    value = json.loads(
        subprocess.check_output(["docker", "inspect", identifier], text=True)
    )[0]
    if (
        value["Config"]["Labels"].get("com.docker.compose.project")
        != "seaweedfs-filesystem-cluster"
    ):
        raise RuntimeError("Refusing an unrelated container")
    return {
        "id": value["Id"],
        "image": value["Image"],
        "started_at": value["State"]["StartedAt"],
        "pid": value["State"]["Pid"],
        "status": value["State"]["Status"],
        "exit_code": value["State"]["ExitCode"],
        "restarts": value["RestartCount"],
    }


def actor(role, args, path):
    stream = path.open("x")
    process = subprocess.Popen(
        [
            *c.COMPOSE,
            "exec",
            "-T",
            "files-" + role,
            "timeout",
            "180",
            "files-workload",
            *args,
        ],
        cwd=c.ROOT,
        text=True,
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
    return process, stream


def locks(directory):
    run = uuid.uuid4().hex
    c.save(
        directory / "probe.json",
        {"run": run, "open_mode": "read-only", "lock": "LOCK_EX|LOCK_NB"},
    )
    process, stream = actor("writer", ["lock-hold", run], directory / "holder.log")
    try:
        wait_for(
            lambda: any(
                r.get("event") == "lock_held" for r in rows(directory / "holder.log")
            ),
            timeout=15,
        )
        contender, output = actor(
            "reader", ["lock-try", run], directory / "contender.log"
        )
        try:
            code = contender.wait(timeout=25)
        finally:
            if contender.poll() is None:
                contender.terminate()
                contender.wait(timeout=5)
            output.close()
        result = (directory / "contender.log").read_text()
        if code:
            raise RuntimeError("Lock contender failed; original output preserved")
        if not any(
            r.get("event") == "lock_denied"
            for r in map(json.loads, result.splitlines())
        ):
            raise RuntimeError("No cross-mount exclusion proof")
        denied = next(
            r
            for r in rows(directory / "contender.log")
            if r.get("event") == "lock_denied"
        )
        if denied["flock_ms"] > 5000 or process.poll() is not None:
            raise RuntimeError("No bounded denial while holder was still running")
        identity = {}
        for role in ("writer", "reader"):
            raw = c.command(
                "exec",
                "-T",
                "files-" + role,
                "stat",
                "--printf=%d %i %s\n",
                "/mnt/lab/" + run + ".lock",
            )
            device, inode, size = map(int, raw.split())
            identity[role] = {"device": device, "inode": inode, "size": size}
        if process.wait(timeout=25):
            raise RuntimeError("Lock holder failed")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        stream.close()
    return {
        "cross_mount_flock": "denied while independent mount held exclusive lock",
        "dlm": True,
        "run": run,
        "mount_file_identity": identity,
    }


def outage(directory):
    fixture_path = c.ROOT / "results/fixture.json"
    fixture = json.loads(fixture_path.read_text())
    if fixture["stage"] != "ready":
        raise RuntimeError("Initial correctness gate has not passed")
    c.audit(directory)
    run = uuid.uuid4().hex
    fixture["runs"].append(run)
    c.save(fixture_path, fixture)
    before = {
        role: inspect(role) for role in [*c.STORAGE, "files-writer", "files-reader"]
    }
    c.save(directory / "before.json", before)
    writer, ws = actor(
        "writer",
        ["writer", run, "512", str(fixture["payload_bytes"])],
        directory / "writer.log",
    )
    reader, rs = actor(
        "reader", ["reader", fixture["runs"][0], run], directory / "reader.log"
    )
    restored = False
    try:
        witness = c.ROOT / "results/journals" / (run + ".jsonl")
        ack_count = lambda: sum(r.get("event") == "ack" for r in rows(witness))
        wait_for(
            lambda: ack_count() >= 5
            and any(
                r.get("event") == "verified" for r in rows(directory / "reader.log")
            )
        )
        start = time.monotonic()
        c.command("kill", "--signal", "SIGKILL", "filer0", "filer1")
        killed = {role: inspect(role) for role in ("filer0", "filer1")}
        c.save(directory / "killed.json", killed)
        if any(
            v["pid"] != 0 or v["exit_code"] != 137 or v["status"] != "exited"
            for v in killed.values()
        ):
            raise RuntimeError("Both filer processes were not killed")
        time.sleep(10)
        c.command("up", "-d", "--wait", "filer0", "filer1")
        restored = True
        at_restore = ack_count()
        cycles = lambda: sum(
            r.get("event") == "read_cycle" for r in rows(directory / "reader.log")
        )
        read_at_restore = cycles()
        wait_for(
            lambda: ack_count() >= at_restore + 5 and cycles() >= read_at_restore + 3
        )
        (c.ROOT / "results" / ("stop-writer-" + run)).touch()
        if writer.wait(timeout=30):
            raise RuntimeError(
                "Live writer failed; ambiguous outcomes remain unqualified"
            )
        verified = c.audit(directory)
        (c.ROOT / "results" / ("stop-reader-" + run)).touch()
        if reader.wait(timeout=30):
            raise RuntimeError("Live historical reader failed")
        after = {role: inspect(role) for role in before}
        c.save(directory / "after.json", after)
        if any(
            before[role] != after[role]
            for role in before
            if not role.startswith("filer")
        ):
            raise RuntimeError("Unrelated roles changed identity during filer loss")
        return verified | {
            "fault": "both filer processes SIGKILL",
            "held_s": 10,
            "fault_through_final_verification_s": time.monotonic() - start,
            "input_reloaded": False,
        }
    finally:
        if not restored:
            c.command("up", "-d", "--wait", "filer0", "filer1")
        for role, process, stream in [("writer", writer, ws), ("reader", reader, rs)]:
            (c.ROOT / "results" / ("stop-" + role + "-" + run)).touch()
            try:
                process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                # Stop only the owned application container; preserve the external witness.
                c.command("stop", "files-" + role)
                process.wait(timeout=10)
            stream.close()


def main():
    mode = sys.argv[1]
    if mode not in ("locks", "filer-failure"):
        raise ValueError(mode)
    directory = c.ROOT / "results" / (mode + "-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    c.save(directory / "status.json", {"passed": False, "mode": mode})
    result = locks(directory) if mode == "locks" else outage(directory)
    c.save(directory / "result.json", result)
    c.save(directory / "status.json", {"passed": True, "mode": mode})
    print(json.dumps({"passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
