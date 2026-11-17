"""Native placement and checksum checks on the example's owned guests only."""

import json
from pathlib import Path
import socket
import sys
import time
from guest import ssh

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
        "deep_scrub_before": previous if deep else None,
        "pg_reports": pgs,
        "osd_metadata": osds,
        "quorum": quorum,
        "crush_rule": rule,
        "status": ceph("status"),
    }
    ROOT.mkdir(exist_ok=True)
    (ROOT / "native-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in [
                    "osds",
                    "hosts",
                    "pgs",
                    "copies_per_pg",
                    "distinct_hosts_per_pg",
                    "deep_scrub",
                ]
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["audit"]:
        raise ValueError("expected audit")
    audit()
