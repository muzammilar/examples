"""Preserve the external witness before stopping or deleting this lab's state."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

import lab


def archive():
    # Starting this existing helper does not format or mutate HDFS metadata.
    lab.command("start", "tools")
    raw = subprocess.run(
        [
            *lab.COMPOSE,
            "exec",
            "-T",
            "--user",
            "hadoop",
            "tools",
            "tar",
            "-C",
            "/witness",
            "-cf",
            "-",
            ".",
        ],
        cwd=lab.ROOT,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    journals = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as bundle:
        for member in bundle.getmembers():
            if not member.isfile():
                if not member.isdir():
                    raise ValueError("Unexpected link/device in witness archive")
                continue
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ValueError("Unsafe archive path")
            content = bundle.extractfile(member).read()
            if member.name.endswith("/journal.jsonl"):
                for line in content.splitlines():
                    json.loads(line)
                journals[member.name] = hashlib.sha256(content).hexdigest()
    fixture_path = lab.ROOT / "results/fixture.json"
    fixture = json.loads(fixture_path.read_text()) if fixture_path.exists() else None
    if fixture:
        for case_path in (lab.ROOT / "results/cases").glob("*/case.json"):
            case = json.loads(case_path.read_text())
            if (
                case["fixture"] == fixture["id"]
                and "./" + case["run_id"] + "/journal.jsonl" not in journals
            ):
                raise ValueError("A known case witness is missing; refuse teardown")
    directory = lab.ROOT / "results" / ("teardown-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    target = directory / "witness.tar"
    with target.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        import os

        os.fsync(stream.fileno())
    if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(raw).digest():
        raise ValueError("Witness export readback differs")
    receipt = {
        "fixture": fixture,
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "journals": journals,
    }
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return directory, receipt


def main():
    mode = sys.argv[1]
    if mode not in ("workload", "infrastructure"):
        raise ValueError(mode)
    # Freeze mutation before exporting the final witness, including one-off actors.
    ids = lab.command(
        "ps", "--all", "-q", "files-writer", "files-reader"
    ).stdout.split()
    for identifier in ids:
        info = json.loads(
            subprocess.check_output(["docker", "inspect", identifier], text=True)
        )[0]
        labels = info["Config"]["Labels"]
        if labels.get("com.docker.compose.project") != "hadoop-metadata-ha":
            raise ValueError("Unexpected workload ownership")
        if info["State"]["Running"]:
            subprocess.run(
                ["docker", "stop", "--time", "20", identifier], check=True, timeout=30
            )
    directory, receipt = archive()
    if mode == "workload":
        # Keep quorum services and all volumes for retained-state recovery.
        lab.command(
            "stop",
            "-t",
            "20",
            "zkfc1",
            "zkfc2",
            "nn1",
            "nn2",
            "dn1",
            "dn2",
            "dn3",
            "witness-control",
            "tools",
        )
    else:
        lab.command(
            "--profile", "tools", "down", "--volumes", "--remove-orphans", timeout=180
        )
        if lab.command("ps", "--all", "-q").stdout.strip():
            raise RuntimeError("Owned containers remain")
        receipt["infrastructure_removed"] = True
        (directory / "completed.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(directory)


if __name__ == "__main__":
    main()
