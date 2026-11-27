"""One retained TaskManager loss with source writes/readers kept live."""

import json
import subprocess
import time
import uuid

import lab


def rows(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_bytes().splitlines(keepends=True)
        if line.endswith(b"\n")
    ]


def actor(mode, path, case):
    value = lab.fixture()
    stream = path.open("x")
    process = subprocess.Popen(
        [
            *lab.COMPOSE,
            "exec",
            "-T",
            "-e",
            "RUN_ID=" + value["id"],
            "-e",
            "CASE_ID=" + case,
            "orders",
            "/orders",
            mode,
        ],
        cwd=lab.ROOT,
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
    return process, stream


def main():
    path = lab.directory("worker-failure")
    value = lab.fixture()
    case = uuid.uuid4().hex
    lab.workload("verify", path)
    job = value["job"]
    before = lab.wait_for(
        lambda: (lambda result: result if result["counts"]["completed"] >= 1 else None)(
            lab.api("/jobs/" + job + "/checkpoints")
        )
    )
    lab.save(path / "before-checkpoints.json", before)
    journal = lab.ROOT / "results" / ("mutate-" + case + "-" + value["id"] + ".jsonl")
    writer, ws = actor("mutate", path / "writer.log", case)
    reader, rs = actor("reader", path / "reader.log", case)
    victim = None
    try:
        lab.wait_for(
            lambda: sum(row["event"] == "ack" for row in rows(journal)) >= 5
            and any(row.get("event") == "read" for row in rows(path / "reader.log"))
        )
        workers = lab.command("ps", "--quiet", "taskmanager").split()
        if len(workers) != 2:
            raise RuntimeError("Two example-owned workers required")
        victim = workers[0]
        original = lab.assert_owned(victim, "taskmanager")
        started = time.monotonic()
        subprocess.run(
            ["docker", "kill", "--signal", "SIGKILL", victim],
            check=True,
            capture_output=True,
        )
        killed = lab.assert_owned(victim, "taskmanager")
        lab.save(
            path / "fault.json",
            {
                "case": case,
                "victim": victim,
                "before_pid": original["State"]["Pid"],
                "after": killed["State"],
                "checkpoint_at_injection": lab.api("/jobs/" + job + "/checkpoints"),
                "hold_s": 10,
            },
        )
        if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
            raise RuntimeError("Worker SIGKILL not proved")
        time.sleep(10)
        subprocess.run(["docker", "start", victim], check=True, capture_output=True)

        def restored():
            status = lab.api("/jobs/" + job + "/checkpoints")
            return (
                status
                if status["counts"]["restored"] > before["counts"]["restored"]
                and lab.api("/jobs/" + job)["state"] == "RUNNING"
                else None
            )

        after = lab.wait_for(restored, timeout=180)
        recovered_s = time.monotonic() - started
        if writer.wait(timeout=90):
            raise RuntimeError("Live source writer failed; reconcile retained receipts")
        (lab.ROOT / "results" / ("stop-reader-" + value["id"] + "-" + case)).touch()
        if reader.wait(timeout=90):
            raise RuntimeError("Live historical reader failed")
        lab.workload("verify", path)
        lab.wait_for(
            lambda: lab.api("/jobs/" + job + "/checkpoints")["counts"]["completed"]
            > before["counts"]["completed"],
            timeout=180,
        )
        lab.native(path)
        lab.save(
            path / "result.json",
            {
                "case": case,
                "fault": "one owned TaskManager SIGKILL",
                "hold_s": 10,
                "native_running_restored_s": recovered_s,
                "after_checkpoints": after,
                "source_paused": False,
                "input_reloaded": False,
            },
        )
        lab.save(path / "status.json", {"passed": True})
        print(json.dumps({"passed": True, "evidence": str(path)}))
    finally:
        if victim:
            subprocess.run(["docker", "start", victim], check=True, capture_output=True)
        for mode, process, stream in (("writer", writer, ws), ("reader", reader, rs)):
            (
                lab.ROOT / "results" / ("stop-" + mode + "-" + value["id"] + "-" + case)
            ).touch()
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                lab.command("stop", "orders")
                process.wait(timeout=15)
            stream.close()


if __name__ == "__main__":
    main()
