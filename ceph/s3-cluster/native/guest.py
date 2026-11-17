"""Only the three example-owned guests and serial-allowlisted virtual disks."""

import subprocess
import sys
import time
import concurrent.futures
import json
from pathlib import Path
import uuid


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


def repair_init():
    if any(
        (Path("/state") / name).exists()
        for name in ["bootstrap.json", "bootstrap-plan.json"]
    ):
        raise RuntimeError(
            "package initialization repair is restricted to unprovisioned guests"
        )
    root = Path("/results") / ("guest-init-recovery-" + uuid.uuid4().hex)
    root.mkdir()

    def repair(node):
        before = ssh(node, "dpkg --audit; cloud-init status --format json", True)
        (root / f"ceph{node}-before.log").write_text(before)
        command = (
            "export DEBIAN_FRONTEND=noninteractive; "
            "if pgrep -x dpkg >/dev/null || pgrep -x apt-get >/dev/null; then echo 'package manager still active'; exit 1; fi; "
            "mkdir -p /mnt/packages; mountpoint -q /mnt/packages || mount -o ro /dev/disk/by-label/CEPHPKGS /mnt/packages; "
            f"exec > /var/log/{root.name}.log 2>&1; "
            "dpkg --configure -a; "
            "apt-get -o Dpkg::Options::=--force-confold --no-remove -y install /mnt/packages/*.deb; "
            "apt-get check; "
            'test -z "$(dpkg --audit)"; '
            "for pkg in ceph-mon ceph-mgr ceph-osd ceph-volume radosgw; do "
            'test "$(dpkg-query -W -f=\'${Version}\' "$pkg")" = 20.2.5-1noble; done; '
            "rm -f /usr/sbin/policy-rc.d; touch /var/lib/cephlab-ready"
        )
        try:
            ssh(node, command)
        finally:
            log = ssh(node, f"tail -n 150 /var/log/{root.name}.log", True)
            (root / f"ceph{node}-repair.log").write_text(log)
        return {"node": node, "packages_consistent": True, "version": "20.2.5-1noble"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(repair, range(3)))
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "ready":
        ready()
    elif sys.argv[1] == "ssh":
        print(ssh(int(sys.argv[2]), sys.argv[3], True))
    elif sys.argv[1] == "repair-init":
        repair_init()
    else:
        raise ValueError("unknown action")
