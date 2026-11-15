"""Read native HAProxy counters; prove every intended gateway is routed and used."""

import csv
import io
import json
import sys
from urllib.request import urlopen
import workload as w

backend, *expected = sys.argv[1:]
with urlopen("http://s3:8405/stats;csv", timeout=10) as response:
    rows = list(csv.DictReader(io.StringIO(response.read().decode().lstrip("# "))))
servers = {
    r["svname"]: r for r in rows if r["pxname"] == backend and r["svname"] in expected
}
if set(servers) != set(expected):
    raise RuntimeError("missing expected proxy backends")
report = {}
for name, row in servers.items():
    if row["status"] != "UP" or int(row["lbtot"]) == 0:
        raise RuntimeError(
            f"gateway is not routed/used: {name}: {row['status']}, {row['lbtot']}"
        )
    report[name] = {
        k: row[k]
        for k in ("status", "check_status", "lbtot", "bin", "bout", "econ", "eresp")
    }
w.save("proxy-audit", report)
print(json.dumps(report, indent=2), flush=True)
