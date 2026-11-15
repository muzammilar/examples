"""Native Ceph checks and fault injection on the example's owned guests only."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from control import ssh

ROOT = Path("/results")
STATE = Path("/state")


def qmp(node, command, arguments=None):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(20)
        s.connect(str(STATE / f"ceph{node}/qmp.sock"))
        f = s.makefile("rwb")
        json.loads(f.readline())
        for name, args in [("qmp_capabilities", {}), (command, arguments or {})]:
            f.write((json.dumps({"execute": name, "arguments": args}) + "\n").encode())
            f.flush()
            while True:
                line = f.readline()
                if not line:
                    if name == "quit":
                        return
                    raise RuntimeError("QMP disconnected")
                r = json.loads(line)
                if "error" in r:
                    raise RuntimeError(str(r["error"]))
                if "return" in r:
                    break
        return r["return"]


def ceph(command):
    return json.loads(ssh(1, "ceph " + command + " -f json", True))


def wait_clean(expected=9, timeout=1200):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        s = ceph("status")
        osds = s["osdmap"]
        pg = s.get("pgmap", {})
        if (
            (
                expected != 9
                or (
                    s["health"]["status"] == "HEALTH_OK"
                    and s["mgrmap"]["available"]
                    and s["mgrmap"]["num_standbys"] == 1
                )
            )
            and osds["num_up_osds"] == expected
            and osds["num_in_osds"] == expected
            and pg.get("pgs_by_state")
            and all(v["state_name"] == "active+clean" for v in pg["pgs_by_state"])
        ):
            if pg.get("num_objects_unfound", 0) or pg.get("num_objects_degraded", 0):
                raise RuntimeError("clean-state counters disagree")
            return time.monotonic() - start
        time.sleep(5)
    raise RuntimeError(f"expected {expected} OSDs and active+clean PGs")


def audit(deep=True):
    wait_clean()
    quorum = ceph("quorum_status")
    if set(quorum["quorum_names"]) != {"ceph0", "ceph1", "ceph2"}:
        raise RuntimeError("MON quorum has not fully recovered")
    status = ceph("status")
    if not status["mgrmap"]["available"] or status["mgrmap"]["num_standbys"] != 1:
        raise RuntimeError("active/standby managers have not recovered")
    for i in range(3):
        ssh(i, f"systemctl is-active ceph-radosgw@rgw.ceph{i}")
    osds = ceph("osd metadata")
    hosts = {int(o["id"]): o["hostname"] for o in osds}
    pools = ceph("osd pool ls detail")
    if not pools or any(p["size"] != 3 or p["min_size"] != 2 for p in pools):
        raise RuntimeError("all RGW pools must use size3/min_size2")
    rule = ceph("osd crush rule dump replicated_rule")
    if not any(
        s.get("type") == "host" and s["op"].startswith("chooseleaf")
        for s in rule["steps"]
    ):
        raise RuntimeError("CRUSH failure domain is not host")
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        pgs = ceph("pg dump pgs")["pg_stats"]
        pools = ceph("osd pool ls detail")
        targets_settled = all(
            p["pg_num"] == p["pg_num_target"]
            and p["pg_placement_num"] == p["pg_placement_num_target"]
            for p in pools
        )
        if (
            targets_settled
            and pgs
            and all(
                p["state"] == "active+clean"
                and len(p["acting"]) == 3
                and len({hosts[o] for o in p["acting"]}) == 3
                for p in pgs
            )
        ):
            break
        print("Waiting for PG/PGP targets and direct placement convergence", flush=True)
        time.sleep(5)
    else:
        raise RuntimeError("PG copies did not converge to three distinct hosts")
    if deep:
        previous = {p["pgid"]: p["last_deep_scrub_stamp"] for p in pgs}
        ssh(1, "ceph osd deep-scrub all")
        print(
            f"Requested deep scrub of {len(pgs)} PGs; waiting for new completion stamps",
            flush=True,
        )
        end = time.monotonic() + 1200
        last_progress = time.monotonic()
        last_completed = 0
        while time.monotonic() < end:
            pgs = ceph("pg dump pgs")["pg_stats"]
            if {p["pgid"] for p in pgs} != set(previous):
                previous = {p["pgid"]: p["last_deep_scrub_stamp"] for p in pgs}
                last_completed = 0
                last_progress = time.monotonic()
                ssh(1, "ceph osd deep-scrub all")
                print(
                    "PG membership changed; restarting scrub coverage check", flush=True
                )
            elif all(
                p["last_deep_scrub_stamp"] != previous[p["pgid"]]
                and p["state"] == "active+clean"
                for p in pgs
            ):
                break
            completed = sum(
                p["last_deep_scrub_stamp"] != previous[p["pgid"]] for p in pgs
            )
            if completed > last_completed:
                last_completed = completed
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress > 60:
                for pending in pgs:
                    if pending["last_deep_scrub_stamp"] != previous[pending["pgid"]]:
                        continue
                    query = ceph("pg " + pending["pgid"] + " query")
                    scrubber = query["scrubber"]
                    if not scrubber["active"] and not scrubber["must_deep_scrub"]:
                        ssh(1, "ceph pg deep-scrub " + pending["pgid"])
                        print(
                            "Rescheduled unscheduled pending PG " + pending["pgid"],
                            flush=True,
                        )
                last_progress = time.monotonic()
            print(
                f"Deep scrub completed on {completed}/{len(pgs)} current PGs",
                flush=True,
            )
            time.sleep(10)
        else:
            raise RuntimeError("deep scrub completion timeout")
        for p in pgs:
            if p["stat_sum"].get("num_scrub_errors", 0) or "inconsistent" in p["state"]:
                raise RuntimeError("native deep scrub found corruption: " + p["pgid"])
    if any(
        len(p["acting"]) != 3 or len({hosts[o] for o in p["acting"]}) != 3 for p in pgs
    ):
        raise RuntimeError("placement changed during native audit")
    pools = ceph("osd pool ls detail")
    if any(p["size"] != 3 or p["min_size"] != 2 for p in pools):
        raise RuntimeError("pool durability changed during audit")
    result = {
        "osds": len(osds),
        "hosts": len(set(hosts.values())),
        "pools": [
            {
                "name": p["pool_name"],
                "size": p["size"],
                "min_size": p["min_size"],
                "pg_num": p["pg_num"],
                "pgp_num": p["pg_placement_num"],
                "pg_num_target": p["pg_num_target"],
                "pgp_num_target": p["pg_placement_num_target"],
            }
            for p in pools
        ],
        "pgs": len(pgs),
        "copies_per_pg": 3,
        "distinct_hosts_per_pg": 3,
        "deep_scrub": deep,
        "status": ceph("status"),
    }
    ROOT.mkdir(exist_ok=True)
    (ROOT / "native-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


def inject(mode):
    ROOT.mkdir(exist_ok=True)
    (ROOT / "surviving-guests.json").write_text(
        json.dumps(
            {
                str(i): ssh(i, "cat /proc/sys/kernel/random/boot_id", True).strip()
                for i in (1, 2)
            }
        )
        + "\n"
    )
    if mode == "process":
        (STATE / "pause0").touch()
        qmp(0, "quit")
    elif mode == "disk":
        if (STATE / "replacement.json").exists():
            raise RuntimeError("disk already replaced; clean lifecycle required")
        devices = json.loads(ssh(0, "lsblk -J -d -o PATH,SERIAL,TYPE", True))[
            "blockdevices"
        ]
        matched = [
            d["path"]
            for d in devices
            if d.get("serial") == "cephlab-ceph0-d0" and d["type"] == "disk"
        ]
        if len(matched) != 1:
            raise RuntimeError("disk serial allowlist mismatch")
        lvs = json.loads(ssh(0, "ceph-volume lvm list --format json", True))
        matches = [
            (int(k), v)
            for k, values in lvs.items()
            for v in values
            if matched[0] in v.get("devices", [])
        ]
        if len(matches) != 1:
            raise RuntimeError("could not uniquely map owned device to OSD")
        osd, claim = matches[0]
        old_fsid = str(uuid.UUID(claim["tags"]["ceph.osd_fsid"]))
        (STATE / "replacement.json").write_text(
            json.dumps({"osd": osd, "osd_fsid": old_fsid, "disk": 0, "spare": 3}) + "\n"
        )
        qmp(0, "device_del", {"id": "data0"})
    else:
        raise ValueError(mode)


def recover(mode):
    if mode == "process":
        (STATE / "pause0").unlink()
        start = time.monotonic()
        while time.monotonic() - start < 600:
            try:
                ssh(0, "ceph -s")
                break
            except subprocess.CalledProcessError:
                time.sleep(5)
        else:
            raise RuntimeError("guest failed to return")
    else:
        info = json.loads((STATE / "replacement.json").read_text())
        osd = int(info["osd"])
        old_fsid = str(uuid.UUID(info["osd_fsid"]))
        ssh(
            0,
            f"systemctl stop ceph-osd@{osd}; systemctl disable ceph-volume@lvm-{osd}-{old_fsid}",
        )
        ssh(1, f"ceph osd out {osd}")
        wait_clean(8)
        ssh(1, f"ceph osd safe-to-destroy {osd}")
        ssh(1, f"ceph osd destroy {osd} --yes-i-really-mean-it")
        ssh(
            0,
            f"if mountpoint -q /var/lib/ceph/osd/ceph-{osd}; then umount /var/lib/ceph/osd/ceph-{osd}; fi; rmdir /var/lib/ceph/osd/ceph-{osd}",
        )
        qmp(
            0,
            "blockdev-add",
            {
                "driver": "qcow2",
                "node-name": "spare",
                "file": {"driver": "file", "filename": "/state/ceph0/disk3.qcow2"},
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
        devices = json.loads(ssh(0, "lsblk -J -d -o PATH,SERIAL,TYPE", True))[
            "blockdevices"
        ]
        paths = [
            d["path"]
            for d in devices
            if d.get("serial") == "cephlab-ceph0-r0" and d["type"] == "disk"
        ]
        if len(paths) != 1:
            raise RuntimeError("replacement serial allowlist mismatch")
        # Persist the replacement mapping so a later guest restart cannot
        # accidentally resurrect the removed disk's old data.
        (STATE / "ceph0/replacements.json").write_text(json.dumps({"0": 3}) + "\n")
        ssh(
            0,
            f"systemctl reset-failed ceph-osd@{osd}; ceph-volume lvm create --bluestore --osd-id {osd} --data {paths[0]}",
        )
        ssh(1, f"ceph osd in {osd}")
    wait_clean()
    if mode == "disk":
        # Verify the empty replacement's persistent attachment and native LVM
        # activation survive a graceful power cycle, with the producer live.
        old_boot = ssh(0, "cat /proc/sys/kernel/random/boot_id", True).strip()
        try:
            ssh(0, "systemctl poweroff --no-block")
        except subprocess.CalledProcessError as error:
            if error.returncode != 255:
                raise
            # SSH may close before reporting command success. The boot-ID
            # barrier below must still prove an actual completed power cycle.
        end = time.monotonic() + 600
        while time.monotonic() < end:
            try:
                new_boot = ssh(0, "cat /proc/sys/kernel/random/boot_id", True).strip()
                if new_boot != old_boot:
                    break
            except subprocess.CalledProcessError:
                pass
            time.sleep(5)
        else:
            raise RuntimeError("replacement guest did not power-cycle")
        wait_clean()
        devices = json.loads(ssh(0, "lsblk -J -d -o PATH,SERIAL,TYPE", True))[
            "blockdevices"
        ]
        if not any(d.get("serial") == "cephlab-ceph0-r0" for d in devices) or any(
            d.get("serial") == "cephlab-ceph0-d0" for d in devices
        ):
            raise RuntimeError("replacement attachment did not persist across reboot")
        (ROOT / "replacement-restart.json").write_text(
            json.dumps(
                {
                    "old_boot_id": old_boot,
                    "new_boot_id": new_boot,
                    "replacement_serial": "cephlab-ceph0-r0",
                    "old_serial_absent": True,
                }
            )
            + "\n"
        )
    before = json.loads((ROOT / "surviving-guests.json").read_text())
    after = {
        str(i): ssh(i, "cat /proc/sys/kernel/random/boot_id", True).strip()
        for i in (1, 2)
    }
    if before != after:
        raise RuntimeError("a surviving guest unexpectedly rebooted")


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "audit":
        audit()
    elif action == "inject":
        inject(sys.argv[2])
    elif action == "recover":
        recover(sys.argv[2])
    else:
        raise ValueError(action)
