"""Copy real acknowledged CephFS bodies through an external owned archive."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from guest import ssh
from workload import scp


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    mode, run, backup, relative = sys.argv[1:]
    if mode not in ("create", "restore") or any(
        not re.fullmatch("[0-9a-f]{32}", value) for value in (run, backup)
    ):
        raise ValueError("Unexpected backup operation/identity")
    source = Path("/results") / relative
    if not source.resolve().is_relative_to(Path("/results")) or not source.is_file():
        raise ValueError("Backup input must be an owned results file")
    started = time.monotonic()
    if mode == "create":
        if source.name != "journal.jsonl":
            raise ValueError("Expected external witness")
        units = ssh(
            1,
            "systemctl list-units --type=service --state=running --no-legend --plain",
            True,
        )
        if any(
            line.split()[0].startswith("cephfs-writer-") for line in units.splitlines()
        ):
            raise RuntimeError("Stop writers before declaring a backup cut")
        root = "/var/lib/cephfs-backup/" + backup + "-" + run
        archive = root + ".tar"
        ssh(1, "mkdir -p /var/lib/cephfs-backup; mkdir " + root)
        scp(1, str(source), root + "/journal.jsonl")
        output = ssh(
            1,
            "timeout 600 /usr/local/bin/cephfs-files backup " + run + " " + root,
            True,
        )
        print(output, end="")
        ssh(1, "tar -C " + root + " -cf " + archive + " journal.jsonl bodies")
        target = Path("/results/backups") / backup / (run + ".tar")
        target.parent.mkdir(parents=True, exist_ok=True)
        scp(1, str(target), archive, download=True)
        with target.open("rb") as durable:
            os.fsync(durable.fileno())
        digest = digest_file(target)
        native = ssh(1, "sha256sum " + archive, True).split()[0]
        if digest != native:
            raise RuntimeError("Exported archive differs from guest backup")
        # Release only our verified temporary guest copies; retain the host archive.
        ssh(1, "rm " + archive + "; rm -r " + root)
        print(
            json.dumps(
                {
                    "event": "backup_archive",
                    "path": str(target.relative_to("/results")),
                    "sha256": digest,
                    "archive_bytes": target.stat().st_size,
                    "elapsed_s": time.monotonic() - started,
                }
            )
        )
    else:
        if source.name != run + ".tar":
            raise ValueError("Archive/run identity mismatch")
        root = "/var/lib/cephfs-files/" + run
        archive = "/var/lib/cephfs-backup/" + backup + "-" + run + ".tar"
        ssh(1, "mkdir -p /var/lib/cephfs-backup /var/lib/cephfs-files; mkdir " + root)
        scp(1, str(source), archive)
        if ssh(1, "sha256sum " + archive, True).split()[0] != digest_file(source):
            raise RuntimeError("Imported archive differs")
        ssh(1, "tar --no-same-owner -C " + root + " -xf " + archive)
        print(
            ssh(
                1,
                "timeout 600 /usr/local/bin/cephfs-files restore " + run + " " + root,
                True,
            ),
            end="",
        )
        ssh(1, "rm " + archive)
        print(
            json.dumps(
                {
                    "event": "archive_restored",
                    "run": run,
                    "elapsed_s": time.monotonic() - started,
                    "regenerated": False,
                }
            )
        )


if __name__ == "__main__":
    main()
