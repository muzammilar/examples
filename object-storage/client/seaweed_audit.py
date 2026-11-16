"""Native volume-ID replication and three-zone placement audit."""

import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

out = Path("/results")
mode = sys.argv[1]
start = time.monotonic()
while True:
    replicas = {}
    for node in range(9):
        with urlopen(
            f"http://volume{node}:8080/status?pretty=y", timeout=15
        ) as response:
            status = json.load(response)
        for volume in status.get("Volumes") or []:
            replicas.setdefault(str(volume["Id"]), []).append(
                {
                    "node": node,
                    "zone": node // 3,
                    "files": volume["FileCount"],
                    "bytes": volume["Size"],
                }
            )
    if not replicas:
        raise RuntimeError("no native volumes found")
    if mode == "snapshot":
        (out / "physical-baseline.json").write_text(json.dumps(sorted(replicas)) + "\n")
        break
    expected = set(json.loads((out / "physical-baseline.json").read_text()))
    incomplete = [
        v for v in expected if len({r["zone"] for r in replicas.get(v, [])}) != 3
    ]
    if not incomplete:
        report = {
            "logical_volumes": len(replicas),
            "replicas": sum(len(v) for v in replicas.values()),
            "missing_zone_replicas": 0,
            "wait_seconds": time.monotonic() - start,
        }
        (out / "physical-recovery.json").write_text(json.dumps(report, indent=2) + "\n")
        (out / "volume-placement.json").write_text(
            json.dumps(replicas, indent=2) + "\n"
        )
        print(report, flush=True)
        break
    if time.monotonic() - start > 600:
        raise RuntimeError("volume repair timeout: " + str(incomplete))
    time.sleep(10)
