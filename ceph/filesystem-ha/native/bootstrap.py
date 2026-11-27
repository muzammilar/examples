"""Native MON/MGR/BlueStore and CephFS provisioning in owned Linux guests."""

import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import uuid
from guest import ssh, ready


def write(node, path, data, mode="600"):
    encoded = base64.b64encode(data.encode()).decode()
    ssh(
        node,
        f"mkdir -p $(dirname {path}); echo {encoded} | base64 -d > {path}; chmod {mode} {path}",
    )


def wait_clean(timeout=900, require_pgs=True):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = json.loads(ssh(0, "ceph status -f json", True))
        states = status.get("pgmap", {}).get("pgs_by_state", [])
        if (
            (states or not require_pgs)
            and all(s["state_name"] == "active+clean" for s in states)
            and status["osdmap"]["num_up_osds"] == 9
            and status["osdmap"]["num_in_osds"] == 9
        ):
            print(json.dumps(status, indent=2), flush=True)
            return
        print("Waiting for 9 OSDs and active+clean PGs", flush=True)
        time.sleep(10)
    raise RuntimeError("Ceph did not return to 9 OSDs / active+clean")


ready()
for node in range(3):
    ssh(node, "apt-get check >/dev/null; ceph-mon --version; ceph-osd --version")
    for daemon in ("mon", "mgr", "osd", "mds", "volume"):
        write(
            node,
            f"/etc/systemd/system/ceph-{daemon}@.service.d/allocator.conf",
            '[Service]\nEnvironment="TCMALLOC_STACKTRACE_METHOD=generic_fp"\n',
            "644",
        )
    ssh(node, "systemctl daemon-reload")
marker = Path("/state/bootstrap.json")
plan = Path("/state/bootstrap-plan.json")
if plan.exists():
    fsid = json.loads(plan.read_text())["fsid"]
else:
    fsid = str(uuid.uuid4())
    plan.write_text(json.dumps({"fsid": fsid, "version": "21.2.0"}) + "\n")
conf = f"""[global]
fsid = {fsid}
mon initial members = ceph0,ceph1,ceph2
mon host = 192.168.65.11,192.168.65.12,192.168.65.13
public network = 192.168.65.0/24
cluster network = 192.168.65.0/24
osd pool default pg num = 8
osd pool default pgp num = 0
osd pool default size = 3
osd pool default min size = 2
osd crush chooseleaf type = 1
osd pool default pg autoscale mode = on
[osd]
osd memory target = 1073741824
bluestore cache autotune = true
"""
for i in range(2):
    conf += f"[mgr.ceph{i}]\nkeyring = /var/lib/ceph/mgr/ceph-ceph{i}/keyring\n"
for i in range(3):
    write(i, "/etc/ceph/ceph.conf", conf, "644")
ssh(
    0,
    "if ! test -f /etc/ceph/ceph.mon.keyring; then ceph-authtool --create-keyring /etc/ceph/ceph.mon.keyring --gen-key -n mon. --cap mon 'allow *'; fi; if ! test -f /etc/ceph/ceph.client.admin.keyring; then ceph-authtool --create-keyring /etc/ceph/ceph.client.admin.keyring --gen-key -n client.admin --cap mon 'allow *' --cap osd 'allow *' --cap mgr 'allow *' --cap mds 'allow *'; fi; ceph-authtool /etc/ceph/ceph.mon.keyring --import-keyring /etc/ceph/ceph.client.admin.keyring",
)
ssh(
    0,
    f"if ! test -f /etc/ceph/monmap.seed; then monmaptool --create --fsid {fsid} --add ceph0 192.168.65.11 --add ceph1 192.168.65.12 --add ceph2 192.168.65.13 /etc/ceph/monmap.seed; fi",
)
key = ssh(0, "cat /etc/ceph/ceph.mon.keyring", True)
admin = ssh(0, "cat /etc/ceph/ceph.client.admin.keyring", True)
monmap = ssh(0, "base64 -w0 /etc/ceph/monmap.seed", True).strip()
for i in range(3):
    write(i, "/etc/ceph/mon-bootstrap.keyring", key)
    write(i, "/etc/ceph/ceph.client.admin.keyring", admin)
    ssh(
        i,
        f"echo {monmap} | base64 -d > /tmp/monmap; mkdir -p /var/lib/ceph/mon/ceph-ceph{i}; chown -R ceph:ceph /var/lib/ceph/mon/ceph-ceph{i}; chown ceph:ceph /etc/ceph/mon-bootstrap.keyring; if ! test -f /var/lib/ceph/mon/ceph-ceph{i}/store.db/CURRENT; then ceph-mon --mkfs -i ceph{i} --monmap /tmp/monmap --keyring /etc/ceph/mon-bootstrap.keyring; fi; chown -R ceph:ceph /var/lib/ceph/mon/ceph-ceph{i}; systemctl enable --now ceph-mon@ceph{i}",
    )
ssh(
    0,
    "timeout 180 ceph -s; ceph mon enable-msgr2; ceph config set mon auth_allow_insecure_global_id_reclaim false; ceph config set global osd_pool_default_pgp_num 0",
)
for i in range(2):
    key = ssh(
        0,
        f"ceph auth get-or-create mgr.ceph{i} mon 'allow profile mgr' osd 'allow *' mds 'allow *'",
        True,
    )
    write(i, f"/var/lib/ceph/mgr/ceph-ceph{i}/keyring", key)
    ssh(
        i,
        f"chown -R ceph:ceph /var/lib/ceph/mgr/ceph-ceph{i}; systemctl enable --now ceph-mgr@ceph{i}",
    )
bootstrap = ssh(
    0,
    "ceph auth get-or-create client.bootstrap-osd mon 'allow profile bootstrap-osd'",
    True,
)


def provision_osds(i):
    write(i, "/var/lib/ceph/bootstrap-osd/ceph.keyring", bootstrap)
    devices = json.loads(ssh(i, "lsblk -J -d -o PATH,SERIAL,TYPE", True))[
        "blockdevices"
    ]
    for d in range(3):
        serial = f"cephlab-ceph{i}-d{d}"
        matches = [
            v["path"]
            for v in devices
            if v.get("serial") == serial and v["type"] == "disk"
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"refusing non-allowlisted disk: {serial} matches {matches}"
            )
        existing = json.loads(ssh(i, "ceph-volume lvm list --format json", True))
        claims = [
            v
            for values in existing.values()
            for v in values
            if matches[0] in v.get("devices", [])
        ]
        if claims:
            if any(v.get("tags", {}).get("ceph.cluster_fsid") != fsid for v in claims):
                raise RuntimeError("refusing a disk claimed by a different cluster")
        else:
            ssh(i, f"ceph-volume lvm create --bluestore --data {matches[0]}")
    ssh(i, "ceph-volume lvm activate --all")


with ThreadPoolExecutor(max_workers=3) as workers:
    list(workers.map(provision_osds, range(3)))

wait_clean(require_pgs=False)
# Metadata and file-data pools deliberately remain separate and replicated.
existing = json.loads(ssh(0, "ceph osd pool ls -f json", True))
for pool in ("labfs_meta", "labfs_data"):
    if pool not in existing:
        ssh(0, f"ceph osd pool create {pool} 8")
    ssh(
        0,
        f"ceph osd pool set {pool} size 3; ceph osd pool set {pool} min_size 2; ceph osd pool set {pool} pg_autoscale_mode off; ceph osd pool set {pool} pgp_num 8; ceph osd pool set {pool} compression_mode none",
    )
filesystems = json.loads(ssh(0, "ceph fs ls -f json", True))
if not any(f["name"] == "labfs" for f in filesystems):
    ssh(0, "ceph fs new labfs labfs_meta labfs_data")
ssh(
    0,
    "ceph fs set labfs max_mds 1; ceph fs set labfs standby_count_wanted 1; ceph config set mds mds_cache_memory_limit 536870912",
)
for i in range(2):
    name = f"fs{i}"
    key = ssh(
        0,
        f"ceph auth get-or-create mds.{name} mon 'allow profile mds' mgr 'allow profile mds' osd 'allow rw tag cephfs *=*' mds 'allow'",
        True,
    )
    write(i, f"/var/lib/ceph/mds/ceph-{name}/keyring", key)
    ssh(0, f"ceph config set mds.{name} mds_join_fs labfs")
    ssh(
        i,
        f"chown -R ceph:ceph /var/lib/ceph/mds/ceph-{name}; systemctl enable --now ceph-mds@{name}",
    )
for client, access in (("lab-writer", "rw"), ("lab-reader", "r")):
    # An unchanged `fs authorize` succeeds without returning a keyring.
    ssh(0, f"ceph fs authorize labfs client.{client} / {access}", True)
    key = ssh(0, f"ceph auth get client.{client}", True)
    if f"[client.{client}]" not in key or "key = " not in key:
        raise RuntimeError("Refusing to replace an owned keyring with non-key output")
    for i in range(3):
        write(i, f"/etc/ceph/ceph.client.{client}.keyring", key)
end = time.monotonic() + 180
while time.monotonic() < end:
    observed = json.loads(ssh(0, "ceph fs dump -f json", True))
    filesystem = next(
        f for f in observed["filesystems"] if f["mdsmap"]["fs_name"] == "labfs"
    )
    active = [
        m for m in filesystem["mdsmap"]["info"].values() if m["state"] == "up:active"
    ]
    if len(active) == 1 and observed["standbys"]:
        break
    time.sleep(2)
else:
    raise RuntimeError("One active and one standby MDS did not appear")
wait_clean()
marker.write_text(
    json.dumps(
        {
            "fsid": fsid,
            "version": "21.2.0",
            "hosts": 3,
            "osds": 9,
            "filesystem": "labfs",
        },
        indent=2,
    )
    + "\n"
)
Path("/results/bootstrap-native.json").write_text(
    json.dumps({"filesystem": filesystem, "standbys": observed["standbys"]}, indent=2)
    + "\n"
)
