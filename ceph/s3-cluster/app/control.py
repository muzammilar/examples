"""Only the three example-owned guests and serial-allowlisted virtual disks."""

import subprocess
import sys
import time


def ssh(node, command, capture=False):
    r = subprocess.run(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=30",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=120",
            "-o",
            "ControlPath=/state/ssh-%r-%p.sock",
            "-i",
            "/state/id_ed25519",
            "-p",
            str(2221 + node),
            "root@127.0.0.1",
            "bash",
            "-ec",
            "'"
            + ("export TCMALLOC_STACKTRACE_METHOD=generic_fp; " + command).replace(
                "'", "'\\''"
            )
            + "'",
        ],
        check=False,
        text=True,
        capture_output=capture,
        timeout=600,
    )
    if r.returncode and capture:
        print(r.stdout, file=sys.stderr)
        print(r.stderr, file=sys.stderr)
    r.check_returncode()
    return r.stdout


def ready():
    end = time.monotonic() + 1800
    remaining = set(range(3))
    while remaining and time.monotonic() < end:
        for i in list(remaining):
            try:
                ssh(i, "test -f /var/lib/cephlab-ready")
                remaining.remove(i)
                print(f"Guest {i} ready", flush=True)
            except subprocess.CalledProcessError:
                pass
        if remaining:
            time.sleep(10)
    if remaining:
        raise RuntimeError(
            f"Guest setup failed/timed out: {remaining}; inspect console and /var/log/ceph-packages.log"
        )
    # Actual guest-to-guest multicast-LAN reachability, not host NAT reachability.
    for i in range(3):
        for j in range(3):
            if i != j:
                ssh(i, f"ping -c 1 -W 3 192.168.65.{11 + j}")


if __name__ == "__main__":
    if sys.argv[1] == "ready":
        ready()
    elif sys.argv[1] == "ssh":
        print(ssh(int(sys.argv[2]), sys.argv[3], True))
    else:
        raise ValueError("unknown action")
