"""Only copy/run the locally built application in the three owned guests."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from guest import ssh


def scp(node, source, target, download=False):
    if node not in range(3):
        raise ValueError("Unowned guest")
    remote = f"root@127.0.0.1:{target}"
    command = [
        "scp",
        "-F",
        "/dev/null",
        "-q",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-i",
        "/state/id_ed25519",
        "-P",
        str(2221 + node),
    ]
    command += [remote, source] if download else [source, remote]
    subprocess.run(command, check=True, timeout=60)


def main():
    mode = sys.argv[1]
    if mode == "install":
        source = Path("/opt/workload/cephfs-files")
        if not source.is_file():
            raise RuntimeError("Build the application first")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        for node in range(3):
            scp(node, str(source), "/usr/local/bin/cephfs-files")
            installed = ssh(
                node, "sha256sum /usr/local/bin/cephfs-files", True
            ).split()[0]
            if installed != digest:
                raise RuntimeError("Guest executable differs from Docker build")
        print(json.dumps({"installed_guests": 3, "binary_sha256": digest}))
        return
    node = int(sys.argv[2])
    run = sys.argv[3]
    if node not in range(3) or not re.fullmatch("[0-9a-f]{32}", run):
        raise ValueError("Unowned workload path")
    root = "/var/lib/cephfs-files/" + run
    if mode == "export":
        out = Path("/results/exports") / (run + "-" + str(time.time_ns()))
        out.mkdir(parents=True)
        scp(node, str(out / "journal.jsonl"), root + "/journal.jsonl", download=True)
        print(
            json.dumps(
                {
                    "export": str(out),
                    "sha256": hashlib.sha256(
                        (out / "journal.jsonl").read_bytes()
                    ).hexdigest(),
                }
            )
        )
    elif mode == "verify":
        source = Path("/results") / sys.argv[4]
        if (
            not source.resolve().is_relative_to(Path("/results"))
            or source.name != "journal.jsonl"
        ):
            raise ValueError("Unowned witness import")
        target = "/var/lib/cephfs-verify/" + run + "-" + str(time.time_ns())
        ssh(node, "mkdir -p " + target)
        scp(node, str(source), target + "/journal.jsonl")
        received = ssh(node, "sha256sum " + target + "/journal.jsonl", True).split()[0]
        if received != hashlib.sha256(source.read_bytes()).hexdigest():
            raise RuntimeError("Guest witness copy differs")
        print(
            ssh(
                node,
                "timeout 180 /usr/local/bin/cephfs-files verify " + run + " " + target,
                True,
            )
        )
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
