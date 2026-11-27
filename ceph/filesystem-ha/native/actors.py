"""Systemd workload actors and local phase controls in allowlisted guests."""

import json
import re
import shlex
import sys
from guest import ssh


def identify(role, run):
    if role not in ("writer", "reader") or not re.fullmatch("[0-9a-f]{32}", run):
        raise ValueError("Unowned actor")
    return (
        (1 if role == "writer" else 2),
        f"cephfs-{role}-{run}.service",
        "/var/lib/cephfs-files/" + run,
    )


def status(node, unit):
    raw = ssh(
        node,
        "systemctl show "
        + unit
        + " -p MainPID -p ActiveState -p SubState -p ExecMainCode -p ExecMainStatus -p CPUUsageNSec -p MemoryPeak -p IOReadBytes -p IOWriteBytes",
        True,
    )
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def main():
    action, role, run = sys.argv[1:4]
    node, unit, root = identify(role, run)
    if action == "start":
        count, size = map(int, sys.argv[4:6])
        source_run = sys.argv[6] if len(sys.argv) == 7 else run
        if not re.fullmatch("[0-9a-f]{32}", source_run) or (
            role == "writer" and source_run != run
        ):
            raise ValueError("Invalid workload data identity")
        if (
            not 1 <= count <= 10000
            or not 1 <= size <= 16777216
            or count * size > 268435456
        ):
            raise ValueError("Unsafe workload size")
        ssh(
            node,
            "mkdir -p /var/log/cephfs-files" + (" " + root if role == "reader" else ""),
        )
        args = [
            "systemd-run",
            "--unit=" + unit,
            "--property=Type=exec",
            "--property=RemainAfterExit=yes",
            "--property=CPUAccounting=yes",
            "--property=MemoryAccounting=yes",
            "--property=IOAccounting=yes",
            "--property=LimitCORE=0",
            "--property=TimeoutStopSec=20",
            "--property=StandardOutput=append:/var/log/cephfs-files/"
            + role
            + "-"
            + run
            + ".jsonl",
            "--property=StandardError=append:/var/log/cephfs-files/"
            + role
            + "-"
            + run
            + ".stderr",
            "--setenv=TCMALLOC_STACKTRACE_METHOD=generic_fp",
            "--setenv=FILES=" + str(count),
            "--setenv=PAYLOAD_BYTES=" + str(size),
            "--setenv=DURATION_S=240",
            "/usr/local/bin/cephfs-files",
            role,
            source_run,
            root,
        ]
        ssh(node, shlex.join(args))
        print(json.dumps(status(node, unit)))
    elif action == "status":
        print(json.dumps(status(node, unit)))
    elif action == "logs":
        print(
            ssh(node, "cat /var/log/cephfs-files/" + role + "-" + run + ".jsonl", True)
        )
    elif action == "phase":
        phase = sys.argv[4]
        if phase not in ("baseline", "injection", "recovery", "after"):
            raise ValueError("Unknown phase")
        ssh(node, "printf %s " + phase + " > " + root + "/phase")
    elif action == "stop":
        marker = "stop" if role == "writer" else "stop-reader"
        ssh(node, "touch " + root + "/" + marker)
    else:
        raise ValueError(action)


if __name__ == "__main__":
    main()
