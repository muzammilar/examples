"""Native HTTP project using the parent example's declared backend lifecycle."""

import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import control as lab


def source_hash():
    source = Path(__file__).parent / "workload"
    parts = [
        hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in ("main.cpp", "payload.h")
    ]
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


def execute(mode, run, *args):
    output = lab.command(
        "exec",
        "-T",
        "http-workload",
        "timeout",
        "180",
        "http-workload",
        mode,
        run,
        *map(str, args),
    )
    rows = [json.loads(line) for line in output.splitlines()]
    if not any(
        row.get("event") == "source" and row.get("sha256") == source_hash()
        for row in rows
    ):
        raise RuntimeError("HTTP executable differs from tracked source")
    return output


def audit(directory):
    fixture = json.loads((ROOT / "results/native-http/fixture.json").read_text())
    parent = json.loads((ROOT / "results/fixture.json").read_text())
    if fixture["parent_fixture"] != parent["id"]:
        raise RuntimeError("HTTP witness belongs to another lab fixture")
    for run in fixture["runs"]:
        for index, endpoint in enumerate(("http://filer0:8888", "http://filer1:8888")):
            output = execute("verify", run, endpoint)
            (directory / (f"http-{index}-" + run + ".log")).write_text(output)
            verified = [
                row
                for row in map(json.loads, output.splitlines())
                if row.get("event") == "verified"
            ]
            if len(verified) != 1 or verified[0]["wrong"] or verified[0]["missing_ack"]:
                raise RuntimeError("No complete HTTP readback")
            expected = fixture.get("run_counts", {}).get(run)
            if expected is not None and verified[0]["acks"] != expected:
                raise RuntimeError("HTTP seed count differs from declared input")


def main():
    mode = sys.argv[1]
    root = ROOT / "results/native-http"
    root.mkdir(parents=True, exist_ok=True)
    directory = root / (mode + "-" + str(time.time_ns()))
    directory.mkdir()
    if mode == "up":
        output = lab.command("build", "http-workload")
        output += lab.command(
            "--profile", "native-http", "up", "-d", "--no-deps", "http-workload"
        )
        (directory / "command.log").write_text(output)
    elif mode == "test":
        path = root / "fixture.json"
        if path.exists():
            raise RuntimeError("HTTP corpus exists; use api-verify, never reseed")
        parent = json.loads((ROOT / "results/fixture.json").read_text())
        if parent["stage"] != "ready":
            raise RuntimeError("Parent correctness baseline has not passed")
        run = parent["runs"][0]
        fixture = {
            "parent_fixture": parent["id"],
            "runs": [run],
            "run_counts": {run: parent["files"]},
            "stage": "initializing",
        }
        lab.save(path, fixture)
        output = execute("seed", run, parent["files"], parent["payload_bytes"])
        (directory / "seed.log").write_text(output)
        result = lab.audit(directory)
        fixture["stage"] = "ready"
        lab.save(path, fixture)
        lab.save(directory / "result.json", result)
    elif mode == "verify":
        audit(directory)
    else:
        raise ValueError(mode)
    print(json.dumps({"mode": mode, "passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
