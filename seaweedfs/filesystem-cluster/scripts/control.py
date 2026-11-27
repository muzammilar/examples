"""Lifecycle and initial correctness gates for the example-owned FUSE mounts."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--project-name", "seaweedfs-filesystem-cluster"]
STORAGE = [
    f"{role}{i}"
    for role, n in [("master", 3), ("etcd", 3), ("volume", 3), ("filer", 2)]
    for i in range(n)
]


def command(*args, timeout=300):
    result = subprocess.run(
        [*COMPOSE, *args], cwd=ROOT, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result.stdout


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def execute(role, *args):
    output = command(
        "exec",
        "-T",
        "files-" + role,
        "timeout",
        "180",
        "files-workload",
        *map(str, args),
    )
    expected = hashlib.sha256(
        (ROOT / "files-workload/main.cpp").read_bytes()
    ).hexdigest()
    if not any(
        row.get("event") == "source" and row.get("sha256") == expected
        for row in map(json.loads, output.splitlines())
    ):
        raise RuntimeError("Workload source hash differs from checkout")
    return output


def audit(directory):
    fixture = json.loads((ROOT / "results/fixture.json").read_text())
    for run in fixture["runs"]:
        for role in ("writer", "reader"):
            output = execute(role, "verify", run)
            (directory / (role + "-" + run + ".log")).write_text(output)
    native = json.loads(
        command("exec", "-T", "files-reader", "python3", "/opt/lab/native_audit.py")
    )
    save(directory / "native-audit.json", native)
    return native


def main():
    mode = sys.argv[1]
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    directory = results / (mode + "-" + str(time.time_ns()))
    directory.mkdir()
    if mode == "docker-up":
        info = json.loads(
            subprocess.check_output(
                ["docker", "info", "--format", "{{json .}}"], text=True
            )
        )
        if info["NCPU"] < 8 or info["MemTotal"] < 8 * 1024**3:
            raise RuntimeError("Needs 8 Docker CPUs / 8 GiB RAM")
        output = command("up", "-d", "--wait", *STORAGE)
    elif mode == "up":
        output = command(
            "--profile",
            "application",
            "up",
            "-d",
            "--wait",
            "files-writer",
            "files-reader",
        )
        output += command("exec", "-T", "files-reader", "weed", "version")
        output += command("exec", "-T", "master0", "weed", "version")
        output += command("exec", "-T", "etcd0", "/usr/local/bin/etcd", "--version")
        if (results / "fixture.json").exists():
            audit(directory)
    elif mode == "test":
        path = results / "fixture.json"
        if path.exists():
            raise RuntimeError("Existing witness: use make verify; never reseed")
        count = int(os.environ.get("FILES", "128"))
        size = int(os.environ.get("PAYLOAD_BYTES", "262144"))
        if (
            not 1 <= count <= 1024
            or not 1 <= size <= 1048576
            or count * size > 268435456
        ):
            raise ValueError("Seed exceeds bounds")
        run = uuid.uuid4().hex
        save(
            path,
            {
                "id": uuid.uuid4().hex,
                "runs": [run],
                "files": count,
                "payload_bytes": size,
                "stage": "initializing",
            },
        )
        output = execute("writer", "seed", run, count, size)
        (directory / "seed.log").write_text(output)
        observed = audit(directory)
        if observed["files"] != count or observed["logical_bytes"] != count * size:
            raise RuntimeError("Native result differs from input")
        fixture = json.loads(path.read_text())
        fixture["stage"] = "ready"
        save(path, fixture)
    elif mode == "verify":
        output = json.dumps(audit(directory))
    elif mode == "down":
        verified = audit(directory)
        output = command(
            "--profile",
            "application",
            "rm",
            "--force",
            "--stop",
            "files-writer",
            "files-reader",
        )
        save(
            results
            / (
                "down-"
                + json.loads((results / "fixture.json").read_text())["id"]
                + ".json"
            ),
            {
                "fixture": json.loads((results / "fixture.json").read_text())["id"],
                "verified": verified,
            },
        )
    elif mode == "docker-down":
        fixture = json.loads((results / "fixture.json").read_text())
        if (
            json.loads((results / ("down-" + fixture["id"] + ".json")).read_text())[
                "fixture"
            ]
            != fixture["id"]
        ):
            raise RuntimeError("Teardown receipt differs from fixture")
        output = command(
            "--profile",
            "application",
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
        )
        if command("ps", "--all", "--quiet").strip():
            raise RuntimeError("Owned containers remain")
        for resource in ("volume", "network"):
            remaining = subprocess.check_output(
                [
                    "docker",
                    resource,
                    "ls",
                    "--filter",
                    "label=com.docker.compose.project=seaweedfs-filesystem-cluster",
                    "--quiet",
                ],
                text=True,
            )
            if remaining.strip():
                raise RuntimeError("Owned " + resource + " resources remain")
        image = "seaweedfs-filesystem-workload:4.55"
        listing = ["docker", "image", "ls", "--filter", "reference=" + image, "--quiet"]
        if subprocess.check_output(listing, text=True).strip():
            subprocess.run(["docker", "image", "rm", image], check=True)
        (results / "fixture.json").rename(
            results / ("retired-fixture-" + fixture["id"] + ".json")
        )
        save(
            results / ("destroyed-" + fixture["id"] + ".json"),
            {
                "fixture": fixture["id"],
                "containers_absent": True,
                "volumes_absent": True,
                "network_absent": True,
                "owned_image_tag_removed": True,
            },
        )
    else:
        raise ValueError(mode)
    (directory / "command.log").write_text(output)
    save(directory / "result.json", {"passed": True, "mode": mode})
    print(json.dumps({"passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
