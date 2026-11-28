"""Fenced logical-file evidence copy; this does not claim native backup/restore qualification."""

import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import lab


def preserve(path, fixture):
    for role in ("files-writer", "files-reader", "cold-reader"):
        identifiers = lab.output(
            "docker",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + lab.PROJECT,
            "--filter",
            "label=com.docker.compose.service=" + role,
            "--quiet",
        ).split()
        if not identifiers:
            continue
        if len(identifiers) != 1:
            raise ValueError("Ambiguous workload owner")
        row = lab.owned(identifiers[0], role)
        if not row["State"]["Running"]:
            continue
        result = subprocess.run(
            ["docker", "exec", identifiers[0], "pgrep", "-x", "files-workload"],
            cwd=lab.ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 1 or result.stderr.strip():
            raise ValueError(
                "Cannot prove workload fencing before logical copy: " + role
            )
    metadata, tree = lab.dump(path)
    expected = {}
    directories = list(fixture["runs"])
    for run in directories:
        entries = tree["FSTree"]["entries"][run].get("entries") or {}
        for name, entry in entries.items():
            if entry["attr"]["type"] != "regular":
                raise ValueError("Unexpected backup entry type")
            expected[run + "/" + name] = entry["attr"]["length"]
    locks = [
        name
        for name, entry in tree["FSTree"]["entries"].items()
        if name.endswith(".lock") and len(name) == 37 and entry["attr"]["length"] == 0
    ]
    expected.update({name: 0 for name in locks})
    archive = path / "logical-files.tar"
    lab.compose(
        "exec",
        "-T",
        "files-reader",
        "tar",
        "-C",
        "/mnt/lab",
        "-cf",
        "/results/" + str(archive.relative_to(lab.ROOT / "results")),
        "--",
        *directories,
        *locks,
        timeout=180
    )
    observed = {}
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            name = member.name.rstrip("/")
            if member.isdir():
                if name not in directories:
                    raise ValueError("Unexpected archive directory")
                continue
            if (
                not member.isfile()
                or name not in expected
                or name in observed
                or member.size != expected[name]
            ):
                raise ValueError("Unexpected, duplicate or changed archive file")
            digest = hashlib.sha256()
            with tar.extractfile(member) as source:
                for value in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(value)
            observed[name] = dict(bytes=member.size, sha256=digest.hexdigest())
    if set(observed) != set(expected):
        raise ValueError("Archive incomplete")
    for run in fixture["runs"]:
        rows = [
            json.loads(line)
            for line in (lab.ROOT / "results/journals" / (run + ".jsonl"))
            .read_text()
            .splitlines()
        ]
        for row in rows:
            if row["event"] == "ack":
                copied = observed[run + "/" + str(row["sequence"])]
                if copied["bytes"] != row["bytes"] or copied["sha256"] != row["sha256"]:
                    raise ValueError("Archived ACK body differs from witness")
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for value in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(value)
    lab.save(path / "logical-copy-files.json", observed)
    receipt = dict(
        passed=True,
        files=len(observed),
        logical_bytes=sum(r["bytes"] for r in observed.values()),
        archive_bytes=archive.stat().st_size,
        archive_sha256=digest.hexdigest(),
        metadata_file=metadata.name,
        metadata_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest(),
        limitation="Fenced plaintext logical evidence copy; native restore and object-orphan coverage not tested",
    )
    lab.save(path / "logical-copy.json", receipt)
    return receipt
