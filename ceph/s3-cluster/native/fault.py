"""Only fenced, serial-allowlisted guest failures and native OSD replacement."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

from guest import ssh
from recovery import ceph, qmp, wait_clean

ROOT = Path("/results")
STATE = Path("/state")


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def devices():
    return json.loads(ssh(0, "lsblk -J -d -o PATH,SERIAL,TYPE", True))["blockdevices"]


def device(serial):
    found = [
        v["path"]
        for v in devices()
        if v.get("serial") == serial and v["type"] == "disk"
    ]
    if len(found) > 1:
        raise RuntimeError("duplicate allowlisted SCSI serial")
    return found[0] if found else None


def claims(path):
    values = json.loads(ssh(0, "ceph-volume lvm list --format json", True))
    return [
        (int(number), value)
        for number, entries in values.items()
        for value in entries
        if path in value.get("devices", [])
    ]


def plan():
    value = json.loads((STATE / "replacement.json").read_text())
    if (
        value["fsid"] != ceph("status")["fsid"]
        or value["disk"] != 0
        or value["spare"] != 3
    ):
        raise RuntimeError("replacement plan belongs to another cluster/device")
    for name in ["fsid", "osd_fsid", "new_osd_fsid"]:
        if str(UUID(value[name])) != value[name]:
            raise RuntimeError("invalid planned UUID")
    if not isinstance(value["osd"], int) or value["osd"] < 0:
        raise RuntimeError("invalid native OSD ID")
    save(ROOT / "replacement.json", value)
    return value


def inject(mode):
    before = {
        str(i): ssh(i, "cat /proc/sys/kernel/random/boot_id", True).strip()
        for i in range(3)
    }
    save(ROOT / "guest-boots-before.json", before)
    if mode == "process":
        (STATE / "pause0").touch()
        qmp(0, "quit")
    elif mode == "disk":
        if (STATE / "replacement.json").exists():
            raise RuntimeError(
                "a replacement plan already exists; diagnose rather than overwrite"
            )
        old = device("cephlab-ceph0-d0")
        found = claims(old) if old else []
        if len(found) != 1:
            raise RuntimeError("cannot uniquely bind owned disk to native OSD")
        number, claim = found[0]
        fsid = ceph("status")["fsid"]
        if claim["tags"]["ceph.cluster_fsid"] != fsid:
            raise RuntimeError("foreign cluster owns the device")
        value = {
            "fsid": fsid,
            "osd": number,
            "osd_fsid": str(UUID(claim["tags"]["ceph.osd_fsid"])),
            "new_osd_fsid": str(uuid4()),
            "disk": 0,
            "spare": 3,
        }
        save(STATE / "replacement.json", value)
        plan()
        qmp(0, "device_del", {"id": "data0"})
        deadline = time.monotonic() + 30
        while device("cephlab-ceph0-d0"):
            if time.monotonic() > deadline:
                raise RuntimeError("old guest device was not detached")
            time.sleep(1)
    else:
        raise ValueError(mode)
    save(ROOT / "injection.json", {"mode": mode, "fault_applied": True})


def verify_replacement(value):
    replacement = device("cephlab-ceph0-r0")
    if not replacement or device("cephlab-ceph0-d0"):
        raise RuntimeError("replacement attachment/fencing is not persistent")
    found = claims(replacement)
    if (
        len(found) != 1
        or found[0][0] != value["osd"]
        or found[0][1]["tags"]["ceph.osd_fsid"] != value["new_osd_fsid"]
    ):
        raise RuntimeError("replacement LVM/native identity differs from the plan")
    actual = {v["osd"]: v["uuid"] for v in ceph("osd dump")["osds"]}
    if actual[value["osd"]] != value["new_osd_fsid"]:
        raise RuntimeError("native OSD UUID differs from replacement LVM")


def replace_disk():
    value = plan()
    number = value["osd"]
    if device("cephlab-ceph0-d0"):
        raise RuntimeError("old disk is still attached; refusing destructive recovery")
    replacement = device("cephlab-ceph0-r0")
    found = claims(replacement) if replacement else []
    if found:
        verify_replacement(value)
        # Re-entry never destroys the already-created replacement.
        ssh(0, f"ceph-volume lvm activate --bluestore {number} {value['new_osd_fsid']}")
    else:
        current = {v["osd"]: v for v in ceph("osd dump")["osds"]}[number]
        if current["uuid"] == value["new_osd_fsid"]:
            raise RuntimeError(
                "partial new preparation has no complete LVM claim; diagnose before changing it"
            )
        if current["uuid"] not in {
            value["osd_fsid"],
            "00000000-0000-0000-0000-000000000000",
        }:
            raise RuntimeError("unexpected OSD UUID; refusing destroy")
        ssh(
            0,
            f"systemctl stop ceph-osd@{number}; systemctl disable ceph-volume@lvm-{number}-{value['osd_fsid']}",
        )
        ssh(1, f"ceph osd out {number}")
        wait_clean(8)
        ssh(1, f"ceph osd safe-to-destroy {number}")
        ssh(1, f"ceph osd destroy {number} --yes-i-really-mean-it")
        path = f"/var/lib/ceph/osd/ceph-{number}"
        ssh(
            0,
            f"if mountpoint -q {path}; then umount {path}; fi; if test -d {path}; then rmdir {path}; fi",
        )
        if not replacement:
            nodes = qmp(0, "query-named-block-nodes")
            if not any(v.get("node-name") == "spare" for v in nodes):
                qmp(
                    0,
                    "blockdev-add",
                    {
                        "driver": "qcow2",
                        "node-name": "spare",
                        "file": {
                            "driver": "file",
                            "filename": "/state/ceph0/disk3.qcow2",
                        },
                        "cache": {"direct": True, "no-flush": False},
                    },
                )
            qmp(
                0,
                "device_add",
                {
                    "driver": "scsi-hd",
                    "drive": "spare",
                    "bus": "scsi.0",
                    "id": "data0",
                    "serial": "cephlab-ceph0-r0",
                },
            )
            ssh(0, "udevadm settle")
            replacement = device("cephlab-ceph0-r0")
        if not replacement:
            raise RuntimeError("replacement serial not visible")
        mapping = STATE / "ceph0/replacements.json"
        if mapping.exists() and json.loads(mapping.read_text()) != {"0": 3}:
            raise RuntimeError("unexpected persisted QEMU disk mapping")
        save(mapping, {"0": 3})
        ssh(
            0,
            f"systemctl reset-failed ceph-osd@{number}; ceph-volume lvm create --bluestore --osd-id {number} --osd-fsid {value['new_osd_fsid']} --data {shlex.quote(replacement)}",
        )
    ssh(1, f"ceph osd in {number}")
    wait_clean()
    verify_replacement(value)


def recover(mode):
    if mode == "process":
        (STATE / "pause0").unlink(missing_ok=True)
        deadline = time.monotonic() + 600
        while True:
            try:
                ssh(0, "test -f /var/lib/cephlab-ready")
                break
            except (OSError, subprocess.SubprocessError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(5)
        wait_clean()
        if (STATE / "replacement.json").exists():
            verify_replacement(plan())
    elif mode == "disk":
        replace_disk()
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    {"inject": inject, "recover": recover}[sys.argv[1]](sys.argv[2])
