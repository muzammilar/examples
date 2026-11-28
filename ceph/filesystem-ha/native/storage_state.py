"""Owned CephFS guest power control and native recovery state."""

import json
from pathlib import Path
import socket
import time
from guest import ssh

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
