"""Native POSIX actors inside only the two owned Ceph client guests."""

import hashlib
import json
from pathlib import Path
import re
import shlex
import sys

sys.path.insert(0, "/opt/lab/native")
from actors import status
from guest import ssh
from workload import scp


def main():
    action, role, case = sys.argv[1:4]
    if role not in ("writer", "reader") or not re.fullmatch("[0-9a-f]{32}", case):
        raise ValueError("Unowned kernel actor")
    node = 1 if role == "writer" else 2
    unit = "cephfs-kernel-" + role + "-" + case + ".service"
    root = "/var/lib/cephfs-kernel"
    log = "/var/log/cephfs-kernel/" + role + "-" + case
    if action == "start":
        run = sys.argv[4]
        if not re.fullmatch("[0-9a-f]{32}", run):
            raise ValueError("Invalid data identity")
        ssh(
            node,
            "mkdir -p /var/log/cephfs-kernel "
            + root
            + "/journals; test ! -e "
            + log
            + ".jsonl",
        )
        args = ["/usr/local/bin/cephfs-posix", role, run]
        if role == "writer":
            count, size = map(int, sys.argv[5:7])
            if (
                run != case
                or not 1 <= count <= 1024
                or not 1 <= size <= 1048576
                or count * size > 268435456
            ):
                raise ValueError("Unsafe kernel writer corpus")
            args += [str(count), str(size)]
        else:
            witness = Path("/results") / sys.argv[5]
            if (
                not witness.resolve().is_relative_to(Path("/results"))
                or witness.name != "journal.jsonl"
            ):
                raise ValueError("Unowned reader witness")
            target = root + "/journals/" + run + ".jsonl"
            scp(node, str(witness), target)
            if (
                ssh(node, "sha256sum " + target, True).split()[0]
                != hashlib.sha256(witness.read_bytes()).hexdigest()
            ):
                raise RuntimeError("Reader witness transfer differs")
            args.append(case)
        command = [
            "systemd-run",
            "--unit=" + unit,
            "--property=Type=exec",
            "--property=RemainAfterExit=yes",
            "--property=CPUAccounting=yes",
            "--property=MemoryAccounting=yes",
            "--property=IOAccounting=yes",
            "--property=LimitCORE=0",
            "--property=TimeoutStopSec=20",
            "--property=StandardOutput=append:" + log + ".jsonl",
            "--property=StandardError=append:" + log + ".stderr",
            "--setenv=WITNESS_ROOT=" + root,
            "timeout",
            "180",
            *args,
        ]
        ssh(node, shlex.join(command))
        print(json.dumps(status(node, unit)))
    elif action == "status":
        print(json.dumps(status(node, unit)))
    elif action == "logs":
        sys.stdout.write(ssh(node, "cat " + log + ".jsonl", True))
    elif action == "stop":
        ssh(node, "touch " + root + "/stop-" + role + "-" + case)
        print(json.dumps(status(node, unit)))
    else:
        raise ValueError(action)


if __name__ == "__main__":
    main()
