"""Remove only this lab's CephFS deployment, retaining the RADOS infrastructure."""

import json
from pathlib import Path
import sys
from guest import ssh


def main():
    expected = sys.argv[1]
    plan = json.loads(Path("/state/bootstrap-plan.json").read_text())
    actual = ssh(0, "ceph fsid", True).strip()
    if expected != actual or expected != plan["fsid"]:
        raise RuntimeError("Refusing another cluster")
    for node in range(3):
        units = ssh(
            node,
            "systemctl list-units --type=service --state=running --no-legend --plain",
            True,
        )
        if any(line.split()[0].startswith("cephfs-") for line in units.splitlines()):
            raise RuntimeError("Workload still running; preserve and stop it first")
    for node in range(2):
        ssh(node, f"systemctl disable --now ceph-mds@fs{node}")
    names = json.loads(ssh(0, "ceph fs ls -f json", True))
    if any(f["name"] == "labfs" for f in names):
        ssh(0, "ceph fs fail labfs; ceph fs rm labfs --yes-i-really-mean-it")
    # This capability is enabled only inside the FSID-checked, disposable lab.
    ssh(0, "ceph config set mon mon_allow_pool_delete true")
    pools = json.loads(ssh(0, "ceph osd pool ls -f json", True))
    for pool in ("labfs_meta", "labfs_data"):
        if pool in pools:
            ssh(0, f"ceph osd pool delete {pool} {pool} --yes-i-really-really-mean-it")
    ssh(0, "ceph config set mon mon_allow_pool_delete false")
    remaining = json.loads(ssh(0, "ceph fs ls -f json", True))
    pools = json.loads(ssh(0, "ceph osd pool ls -f json", True))
    if any(f["name"] == "labfs" for f in remaining) or any(
        p in pools for p in ("labfs_meta", "labfs_data")
    ):
        raise RuntimeError("CephFS teardown incomplete")
    print(
        json.dumps({"fsid": actual, "filesystem_removed": True, "pools_removed": True})
    )


if __name__ == "__main__":
    main()
