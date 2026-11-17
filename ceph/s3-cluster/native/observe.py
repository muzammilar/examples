"""Native cluster identity, guest boot IDs and exact systemd process witnesses."""

import concurrent.futures
import json
import shlex
import subprocess
import sys
from pathlib import Path

from guest import ssh
from recovery import ceph

ROOT = Path("/results")
PROPERTIES = [
    "Id",
    "MainPID",
    "ActiveState",
    "SubState",
    "NRestarts",
    "ExecMainStartTimestampMonotonic",
]


def guest_state(node, osds):
    units = [f"ceph-mon@ceph{node}.service", f"ceph-radosgw@rgw.ceph{node}.service"]
    if node < 2:
        units.append(f"ceph-mgr@ceph{node}.service")
    units.extend(
        f"ceph-osd@{number}.service"
        for number, host in osds.items()
        if host == f"ceph{node}"
    )
    output = ssh(
        node,
        "systemctl show --no-pager "
        + " ".join(map(shlex.quote, units))
        + " --property="
        + ",".join(PROPERTIES),
        True,
    )
    states = {}
    for block in output.strip().split("\n\n"):
        value = dict(line.split("=", 1) for line in block.splitlines())
        name = value.pop("Id")
        if name in states:
            raise RuntimeError("duplicate native unit observation")
        states[name] = value
    if set(states) != set(units):
        raise RuntimeError("incomplete or duplicate native unit observation")
    return {
        "boot_id": ssh(node, "cat /proc/sys/kernel/random/boot_id", True).strip(),
        "units": states,
        "block_devices": json.loads(ssh(node, "lsblk -J -d -o PATH,SERIAL,TYPE", True)),
        "lvm": json.loads(ssh(node, "ceph-volume lvm list --format json", True)),
    }


def snapshot():
    metadata = ceph("osd metadata")
    hosts = {str(int(value["id"])): value["hostname"] for value in metadata}
    if len(hosts) != 9 or set(hosts.values()) != {"ceph0", "ceph1", "ceph2"}:
        raise RuntimeError("unexpected native OSD inventory")
    osds = {
        str(value["osd"]): {"uuid": value["uuid"], "host": hosts[str(value["osd"])]}
        for value in ceph("osd dump")["osds"]
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        guests = list(pool.map(lambda node: guest_state(node, hosts), range(3)))
    status = ceph("status")
    result = {
        "fsid": status["fsid"],
        "osds": osds,
        "guests": {str(i): value for i, value in enumerate(guests)},
        "versions": ceph("versions"),
        "status": status,
        "quorum": ceph("quorum_status"),
    }
    (ROOT / "native-state.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"fsid": result["fsid"], "osds": osds}), flush=True)


def logs():
    result = {}
    for node in range(3):
        try:
            result[str(node)] = {
                "journal": ssh(
                    node,
                    "journalctl --no-pager -n 2000 -o short-iso-precise -u 'ceph*'",
                    True,
                )
            }
        except (subprocess.SubprocessError, OSError) as error:
            result[str(node)] = {"unavailable": str(error)}
    (ROOT / "guest-journals.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    {"snapshot": snapshot, "logs": logs}[sys.argv[1]]()
