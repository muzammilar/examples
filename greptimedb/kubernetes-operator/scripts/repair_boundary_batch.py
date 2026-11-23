"""Explicit repair of this recorded failed fixture; never part of test-production.

Replay only the 34 absent hosts from the external deterministic fixture. This is
manual recovery, not evidence of automatic durability. No table is dropped.
"""

import json
import os
from runtime import ROOT, query

report = ROOT / "manual-boundary-repair.json"
if report.exists():
    raise RuntimeError("repair already attempted; inspect the preserved report")
fixture = json.loads((ROOT / "fixture.json").read_text())
if fixture["series"] != 10000 or fixture["points"] != 100:
    raise RuntimeError(
        "this recovery is specific to the recorded million-sample failure"
    )
acks = [json.loads(line) for line in (ROOT / "journal.jsonl").read_text().splitlines()]
if not any(
    record.get("system") == "greptime"
    and record.get("phase") == "ack-metrics"
    and record.get("first") == 6600
    and record.get("last") == 6700
    for record in acks
):
    raise RuntimeError(
        "external journal does not acknowledge the affected metrics batch"
    )
first, last = 6666, 6700
count = query("SELECT count(*) FROM fleet_load")[0][0]
if count != 996600:
    raise RuntimeError(f"unexpected baseline count {count}; diagnose before repair")
if query(
    "SELECT count(*) FROM fleet_load WHERE host >= 'h006666' AND host < 'h006700'"
) != [[0]]:
    raise RuntimeError("boundary range is not entirely absent; refusing overwrite")
state = {
    "status": "started",
    "before_samples": count,
    "expected_replayed_samples": 3400,
    "source": "external deterministic fixture; acknowledged batch retained in journal.jsonl",
    "fixture": fixture,
    "acked_hosts": [],
}


def save():
    with report.open("w") as output:
        json.dump(state, output, indent=2)
        output.flush()
        os.fsync(output.fileno())


save()
for host in range(first, last):
    values = ",".join(
        f"('h{host:06d}','{host % 100}',{fixture['start_ms'] + point * 1000},{host * 1000 + point})"
        for point in range(fixture["points"])
    )
    state["attempting_host"] = host
    save()
    query(
        "INSERT INTO fleet_load(host,tenant,greptime_timestamp,greptime_value) VALUES "
        + values
    )
    state["acked_hosts"].append(host)
    save()
state["after_samples"] = query("SELECT count(*) FROM fleet_load")[0][0]
state["status"] = "replayed; full correctness verification still required"
save()
if state["after_samples"] != 1000000:
    raise RuntimeError("repair did not restore the expected count")
print(json.dumps(state, indent=2))
