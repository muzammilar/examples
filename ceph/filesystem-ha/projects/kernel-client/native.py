"""Only mount the owned CephFS inside the owned guests; never the Docker host."""

import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, "/opt/lab/native")
from guest import ssh
from workload import scp


def upgrade_clients():
    records = []
    for node in (1, 2):
        version = ssh(node, "uname -r", True).strip()
        if int(version.split(".")[0]) >= 7:
            continue
        before = ssh(node, "cat /proc/sys/kernel/random/boot_id", True).strip()
        ssh(
            node,
            "apt-get clean; env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends linux-image-7.0.0-44-generic=7.0.0-44.44~24.04.1 linux-modules-7.0.0-44-generic=7.0.0-44.44~24.04.1 > /var/log/ceph-kernel-upgrade.log 2>&1",
        )
        output = ssh(node, "cat /var/log/ceph-kernel-upgrade.log", True)
        path = Path("/results/kernel-client") / (
            "kernel-upgrade-" + str(node) + "-" + str(time.time_ns()) + ".log"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output)
        ssh(
            node,
            "test -f /boot/vmlinuz-7.0.0-44-generic; systemd-run --unit=cephlab-kernel-reboot --on-active=2s /usr/sbin/reboot",
            True,
        )
        end = time.monotonic() + 240
        while time.monotonic() < end:
            try:
                boot = ssh(node, "cat /proc/sys/kernel/random/boot_id", True).strip()
                current = ssh(node, "uname -r", True).strip()
                if boot != before and current == "7.0.0-44-generic":
                    break
            except subprocess.CalledProcessError:
                pass
            time.sleep(5)
        else:
            raise RuntimeError("Owned guest did not reboot into pinned HWE kernel")
        end = time.monotonic() + 300
        while time.monotonic() < end:
            status = json.loads(ssh(0, "ceph status -f json", True))
            if (
                status["osdmap"]["num_up_osds"] == 9
                and status["osdmap"]["num_in_osds"] == 9
                and status["pgmap"]["pgs_by_state"]
                and all(
                    r["state_name"] == "active+clean"
                    for r in status["pgmap"]["pgs_by_state"]
                )
            ):
                break
            time.sleep(5)
        else:
            raise RuntimeError(
                "Native redundancy not restored after guest kernel upgrade"
            )
        records.append(
            {
                "node": node,
                "old_kernel": version,
                "new_kernel": current,
                "old_boot": before,
                "new_boot": boot,
                "log": str(path),
            }
        )
    return records


def put(node, path, contents):
    encoded = base64.b64encode(contents.encode()).decode()
    ssh(node, "printf %s " + encoded + " | base64 -d > " + path + "; chmod 600 " + path)


def mount(node):
    role = "lab-writer" if node == 1 else "lab-reader"
    fsid = ssh(0, "ceph fsid", True).strip()
    ssh(node, "modprobe ceph; mkdir -p /mnt/lab")
    key = ssh(0, "ceph auth get-key client." + role, True).strip()
    if not key or "\n" in key:
        raise RuntimeError("No owned CephX key returned")
    put(node, "/etc/ceph/kernel-client.secret", key + "\n")
    existing = ssh(node, "findmnt -J -M /mnt/lab || test $? = 1", True).strip()
    if not existing:
        options = "mon_addr=192.168.65.11:3300/192.168.65.12:3300/192.168.65.13:3300,secretfile=/etc/ceph/kernel-client.secret,ms_mode=secure"
        if node == 2:
            options += ",ro"
        ssh(
            node,
            "timeout 60 mount -t ceph "
            + role
            + "@"
            + fsid
            + ".labfs=/ /mnt/lab -o "
            + options,
        )
    observed = json.loads(
        ssh(node, "findmnt -J -M /mnt/lab -o TARGET,SOURCE,FSTYPE,OPTIONS", True)
    )["filesystems"][0]
    if observed["fstype"] != "ceph" or fsid not in observed["source"]:
        raise RuntimeError("Mount is not the owned Ceph filesystem")
    options = set(observed["options"].split(","))
    if (
        "ms_mode=secure" not in options
        or ("ro" if node == 2 else "rw") not in options
        or not observed["source"].startswith(role + "@")
    ):
        raise RuntimeError(
            "Effective kernel mount permissions/transport differ from contract"
        )
    return observed


def main():
    mode = sys.argv[1]
    if mode == "up":
        upgrades = upgrade_clients()
        source = Path("/opt/kernel-workload/files-workload")
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        result = {}
        for node in (1, 2):
            scp(node, str(source), "/usr/local/bin/cephfs-posix")
            if (
                ssh(node, "sha256sum /usr/local/bin/cephfs-posix", True).split()[0]
                != expected
            ):
                raise RuntimeError("Guest binary differs from Docker build")
            result[str(node)] = {
                "mount": mount(node),
                "kernel": ssh(node, "uname -r", True).strip(),
                "module_vermagic": ssh(node, "modinfo -F vermagic ceph", True).strip(),
            }
        print(
            json.dumps(
                {
                    "guests": result,
                    "binary_sha256": expected,
                    "kernel_upgrades": upgrades,
                    "auth_downgraded": False,
                }
            )
        )
        return
    if mode == "down":
        fsid = ssh(0, "ceph fsid", True).strip()
        for node in (1, 2):
            raw = ssh(
                node, "findmnt -J -M /mnt/lab -o SOURCE,FSTYPE || test $? = 1", True
            ).strip()
            if raw:
                entry = json.loads(raw)["filesystems"][0]
                if entry["fstype"] != "ceph" or fsid not in entry["source"]:
                    raise RuntimeError("Refusing an unrelated guest mount")
                ssh(node, "timeout 30 umount /mnt/lab")
        print(json.dumps({"unmounted": 2}))
        return
    node = int(sys.argv[2])
    run = sys.argv[3]
    if node not in (1, 2) or not re.fullmatch("[0-9a-f]{32}", run):
        raise ValueError("Unexpected workload identity")
    if mode == "run":
        action = sys.argv[4]
        if action not in ("seed", "verify", "writer", "reader"):
            raise ValueError(action)
        args = sys.argv[5:]
        if action in ("seed", "writer"):
            count, size = map(int, args)
            if (
                not 1 <= count <= 1024
                or not 1 <= size <= 1048576
                or count * size > 268435456
            ):
                raise ValueError("Unsafe workload size")
        root = "/var/lib/cephfs-kernel"
        print(
            ssh(
                node,
                shlex.join(
                    [
                        "env",
                        "WITNESS_ROOT=" + root,
                        "timeout",
                        "180",
                        "cephfs-posix",
                        action,
                        run,
                        *args,
                    ]
                ),
                True,
            )
        )
    elif mode == "export":
        directory = Path("/results/kernel-client/exports") / (
            run + "-" + str(time.time_ns())
        )
        directory.mkdir(parents=True)
        scp(
            node,
            str(directory / "journal.jsonl"),
            "/var/lib/cephfs-kernel/journals/" + run + ".jsonl",
            download=True,
        )
        print(
            json.dumps(
                {
                    "path": str(directory / "journal.jsonl"),
                    "sha256": hashlib.sha256(
                        (directory / "journal.jsonl").read_bytes()
                    ).hexdigest(),
                }
            )
        )
    elif mode == "namespace":
        program = (
            "import json,os; entries=list(os.scandir('/mnt/lab/"
            + run
            + "')); print(json.dumps({'run':'"
            + run
            + "','node':"
            + str(node)
            + ",'names':sorted(entry.name for entry in entries),'all_regular_files':all(entry.is_file(follow_symlinks=False) for entry in entries)}))"
        )
        print(ssh(node, "python3 -c " + shlex.quote(program), True))
    elif mode == "verify":
        path = Path("/results") / sys.argv[4]
        if (
            not path.resolve().is_relative_to(Path("/results"))
            or path.name != "journal.jsonl"
        ):
            raise ValueError("Unowned witness path")
        target = "/var/lib/cephfs-kernel-verify/" + run + "-" + str(time.time_ns())
        ssh(node, "mkdir -p " + target + "/journals")
        scp(node, str(path), target + "/journals/" + run + ".jsonl")
        if (
            ssh(
                node, "sha256sum " + target + "/journals/" + run + ".jsonl", True
            ).split()[0]
            != hashlib.sha256(path.read_bytes()).hexdigest()
        ):
            raise RuntimeError("Witness transfer differs")
        print(
            ssh(
                node,
                shlex.join(
                    [
                        "env",
                        "WITNESS_ROOT=" + target,
                        "timeout",
                        "180",
                        "cephfs-posix",
                        "verify",
                        run,
                    ]
                ),
                True,
            )
        )
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
