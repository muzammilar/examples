"""Kernel-client project using the parent lab's owned infrastructure and witness."""

import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import lab
import test as native_checks


def command(*args):
    return lab.command(
        "run",
        "--rm",
        "control",
        "/opt/lab/kernel-client/native.py",
        *map(str, args),
        timeout=1800,
    )


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def audit(directory, fixture=None):
    if fixture is None:
        fixture = json.loads((ROOT / "results/kernel-client/fixture.json").read_text())
    if native_checks.fixture()["fsid"] != fixture["fsid"]:
        raise RuntimeError("Kernel witness belongs to another cluster")
    copied = json.loads(command("export", 1, fixture["run"]).stdout)
    path = ROOT / "results" / Path(copied["path"]).relative_to("/results")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != copied["sha256"] or not raw.endswith(b"\n"):
        raise RuntimeError("Incomplete or changed witness export")
    attempts = {}
    acks = {}
    for row in map(json.loads, raw.splitlines()):
        seq = row["sequence"]
        size = row["bytes"]
        if (
            type(seq) is not int
            or not 0 <= seq < fixture["files"]
            or size != fixture["payload_bytes"]
            or row["run"] != fixture["run"]
        ):
            raise RuntimeError("Witness identity/size differs")
        expected = hashlib.sha256(
            hashlib.shake_256(f"42193:{fixture['run']}:{seq}".encode()).digest(size)
        ).hexdigest()
        if row["sha256"] != expected:
            raise RuntimeError("Body differs from native libcephfs corpus")
        if row["event"] == "attempt" and seq not in attempts:
            attempts[seq] = row
        elif row["event"] == "ack" and seq in attempts and seq not in acks:
            acks[seq] = row
        else:
            raise RuntimeError("Ambiguous/duplicate publication requires diagnosis")
    if len(acks) != fixture["files"] or len(attempts) != len(acks):
        raise RuntimeError("Seed count differs from the acknowledged corpus")
    source = hashlib.sha256(
        (Path(__file__).parent / "posix-workload-source/main.cpp").read_bytes()
    ).hexdigest()
    for node in (1, 2):
        result = command(
            "verify", node, fixture["run"], str(path.relative_to(ROOT / "results"))
        )
        (directory / ("guest-" + str(node) + ".log")).write_text(
            result.stdout + result.stderr
        )
        rows = native_checks.events(result.stdout)
        if not any(
            r.get("event") == "publication_contract"
            and r.get("syncfs") is True
            and r.get("payload_seed_prefix") == "42193"
            for r in rows
        ):
            raise RuntimeError(
                "Kernel publication contract differs from matched native corpus"
            )
        if not any(
            r.get("event") == "source" and r.get("sha256") == source for r in rows
        ):
            raise RuntimeError("Installed POSIX source differs from checkout")
        if not any(
            r.get("event") == "verified"
            and r["acks"] == len(acks)
            and r["missing_ack"] == 0
            and r["wrong"] == 0
            for r in rows
        ):
            raise RuntimeError("No exact kernel-client readback")
        namespace = json.loads(command("namespace", node, fixture["run"]).stdout)
        save(directory / ("guest-" + str(node) + "-namespace.json"), namespace)
        expected_names = sorted(str(sequence) for sequence in range(fixture["files"]))
        if namespace["names"] != expected_names or not namespace["all_regular_files"]:
            raise RuntimeError(
                "Kernel publication namespace differs; retain unexpected staging entries"
            )
    result = {
        "acks": len(acks),
        "bytes": len(acks) * fixture["payload_bytes"],
        "wrong": 0,
        "missing_ack": 0,
        "witness_sha256": copied["sha256"],
        "same_payload_as_libcephfs": True,
        "publication_syncfs": True,
    }
    save(directory / "result.json", result)
    return result


def trial_fixtures():
    parent = native_checks.fixture()
    for path in sorted((ROOT / "results/kernel-client/trials").glob("*/fixture.json")):
        fixture = json.loads(path.read_text())
        if fixture["parent_fixture"] == parent["id"]:
            if fixture["fsid"] != parent["fsid"]:
                raise RuntimeError("Trial cluster differs from its declared parent")
            yield path, fixture


def main():
    mode = sys.argv[1]
    root = ROOT / "results/kernel-client"
    root.mkdir(parents=True, exist_ok=True)
    directory = root / (mode + "-" + str(time.time_ns()))
    directory.mkdir()
    if mode == "up":
        save(
            root / "environment.json",
            {"fsid": native_checks.ssh(0, "ceph fsid").stdout.strip(), "ready": False},
        )
        output = command("up")
        (directory / "mounts.json").write_text(output.stdout)
        save(
            root / "environment.json",
            {"fsid": native_checks.ssh(0, "ceph fsid").stdout.strip(), "ready": True},
        )
    elif mode == "test":
        path = root / "fixture.json"
        if path.exists():
            raise RuntimeError(
                "Kernel witness exists; use kernel-verify instead of reseeding"
            )
        data = native_checks.fixture()
        if data["stage"] != "ready":
            raise RuntimeError("Run the parent baseline first")
        if data["files"] > 1024 or data["payload_bytes"] > 1048576:
            raise RuntimeError("Kernel comparison exceeds the bounded POSIX corpus")
        value = {
            "fsid": data["fsid"],
            "parent_fixture": data["id"],
            "run": data["run"],
            "files": data["files"],
            "payload_bytes": data["payload_bytes"],
            "stage": "initializing",
        }
        save(path, value)
        output = command(
            "run", 1, data["run"], "seed", data["files"], data["payload_bytes"]
        )
        (directory / "seed.log").write_text(output.stdout + output.stderr)
        audit(directory)
        value["stage"] = "ready"
        save(path, value)
    elif mode == "verify":
        audit(directory)
        for path, trial in trial_fixtures():
            trial_directory = directory / path.parent.name
            trial_directory.mkdir()
            audit(trial_directory, trial)
    elif mode == "reopen":
        save(
            directory / "status.json",
            {"passed": False, "case": "retained client remount"},
        )
        audit(directory)
        output = command("down")
        (directory / "unmount.json").write_text(output.stdout)
        output = command("up")
        (directory / "remount.json").write_text(output.stdout)
        audit(directory)
        save(
            directory / "status.json",
            {
                "passed": True,
                "case": "retained client remount",
                "input_reloaded": False,
            },
        )
    elif mode == "down":
        if (root / "fixture.json").exists():
            audit(directory)
            for path, trial in trial_fixtures():
                trial_directory = directory / path.parent.name
                trial_directory.mkdir()
                audit(trial_directory, trial)
        output = command("down")
        (directory / "unmounted.json").write_text(output.stdout)
    else:
        raise ValueError(mode)
    print(json.dumps({"mode": mode, "passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
