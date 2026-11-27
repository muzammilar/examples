"""Live HTTP writer/reader through proven simultaneous filer process loss."""

import json
import subprocess
import time
import uuid

import http_project as http
import production


def actor(arguments, path):
    stream = path.open("x")
    process = subprocess.Popen(
        [
            *http.lab.COMPOSE,
            "exec",
            "-T",
            "http-workload",
            "timeout",
            "180",
            "http-workload",
            *arguments,
        ],
        cwd=http.ROOT,
        stdout=stream,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, stream


def main():
    directory = (
        http.ROOT / "results/native-http" / ("filer-failure-" + str(time.time_ns()))
    )
    directory.mkdir()
    http.lab.save(directory / "status.json", {"passed": False})
    http.lab.audit(directory)
    path = http.ROOT / "results/native-http/fixture.json"
    fixture = json.loads(path.read_text())
    if fixture["stage"] != "ready":
        raise RuntimeError("HTTP correctness gate has not passed")
    run = uuid.uuid4().hex
    fixture["runs"].append(run)
    http.lab.save(path, fixture)
    before = {
        role: production.inspect(role) for role in [*http.lab.STORAGE, "http-workload"]
    }
    http.lab.save(directory / "before.json", before)
    writer, ws = actor(["writer", run, "512", "262144"], directory / "writer.log")
    reader, rs = actor(["reader", fixture["runs"][0], run], directory / "reader.log")
    restored = False
    stop_root = http.ROOT / "results/native-http"
    try:
        journal = stop_root / "journals" / (run + ".jsonl")
        ack_count = lambda: sum(
            row.get("event") == "ack" for row in production.rows(journal)
        )
        cycles = lambda: sum(
            row.get("event") == "read_cycle"
            for row in production.rows(directory / "reader.log")
        )
        production.wait_for(lambda: ack_count() >= 5 and cycles() >= 1)
        started = time.monotonic()
        http.lab.command("kill", "--signal", "SIGKILL", "filer0", "filer1")
        killed = {role: production.inspect(role) for role in ("filer0", "filer1")}
        http.lab.save(directory / "killed.json", killed)
        if any(
            row["pid"] != 0 or row["exit_code"] != 137 or row["status"] != "exited"
            for row in killed.values()
        ):
            raise RuntimeError("Filer kill was not effective")
        time.sleep(10)
        http.lab.command("up", "-d", "--wait", "filer0", "filer1")
        restored = True
        count, reads = ack_count(), cycles()
        production.wait_for(lambda: ack_count() >= count + 5 and cycles() >= reads + 3)
        recovered_s = time.monotonic() - started
        (stop_root / ("stop-writer-" + run)).touch()
        if writer.wait(timeout=30):
            raise RuntimeError("Writer failed; preserve ambiguous outcomes")
        (stop_root / ("stop-reader-" + run)).touch()
        if reader.wait(timeout=30):
            raise RuntimeError("Reader failed; preserve incorrect result")
        verified = http.lab.audit(directory)
        after = {role: production.inspect(role) for role in before}
        http.lab.save(directory / "after.json", after)
        if any(
            before[role] != after[role]
            for role in before
            if not role.startswith("filer")
        ):
            raise RuntimeError("Non-filer role changed during outage")
        http.lab.save(
            directory / "result.json",
            {
                "run": run,
                "fault": "both filers SIGKILL",
                "held_s": 10,
                "fault_to_resumed_progress_s": recovered_s,
                "input_reloaded": False,
                "verified": verified,
            },
        )
        http.lab.save(directory / "status.json", {"passed": True})
        print(json.dumps({"passed": True, "evidence": str(directory)}))
    finally:
        if not restored:
            http.lab.command("up", "-d", "--wait", "filer0", "filer1")
        for role, process, stream in (("writer", writer, ws), ("reader", reader, rs)):
            (stop_root / ("stop-" + role + "-" + run)).touch()
            try:
                process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                http.lab.command("stop", "http-workload")
                process.wait(timeout=10)
            stream.close()


if __name__ == "__main__":
    main()
