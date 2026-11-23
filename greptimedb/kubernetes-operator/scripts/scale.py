"""Scale through the operator only after native region migration/draining."""

import json
from pathlib import Path
import subprocess
import sys
import time

from live_observer import LiveObserver

from runtime import now, wait_baseline, wait_progress

KUBE = ["kubectl", "--context", "kind-greptime-operator", "--namespace", "greptime-lab"]
root = Path("results")
mode = sys.argv[1]
if mode not in ("out", "in"):
    raise ValueError(mode)
case = "k8s-scale-" + mode + "-" + now().strftime("%Y%m%dT%H%M%S")


def kubectl(*args, capture=False):
    return subprocess.run(
        [*KUBE, *args], check=True, text=True, capture_output=capture
    ).stdout


def query(sql):
    return json.loads(
        kubectl("exec", "client", "--", "client", "sql", sql, capture=True)
    )


def active_peers():
    pods = json.loads(kubectl("get", "pods", "-o", "json", capture=True))["items"]
    addresses = {
        p["status"]["podIP"]
        for p in pods
        if p["metadata"]["name"].startswith("telemetry-datanode-")
        and not p["metadata"].get("deletionTimestamp")
        and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in p["status"].get("conditions", [])
        )
    }
    # cluster_info retains drained historical peers. Count only registered IDs
    # whose native RPC address belongs to a currently ready datanode pod.
    return {
        int(row[0])
        for row in query(
            "SELECT peer_id,peer_addr FROM information_schema.cluster_info WHERE peer_type='DATANODE'"
        )
        if row[1].split(":")[0] in addresses
    }


def patch(count):
    kubectl(
        "patch",
        "greptimedbcluster",
        "telemetry",
        "--type",
        "merge",
        "-p",
        json.dumps({"spec": {"datanode": {"replicas": count}}}),
    )
    end = time.monotonic() + 300
    while time.monotonic() < end:
        pods = json.loads(kubectl("get", "pods", "-o", "json", capture=True))["items"]
        datanodes = [
            p
            for p in pods
            if any(
                "datanode" in c.get("command", []) + c.get("args", [])
                for c in p["spec"]["containers"]
            )
        ]
        ready = [
            p
            for p in datanodes
            if not p["metadata"].get("deletionTimestamp")
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in p["status"].get("conditions", [])
            )
        ]
        if len(datanodes) == count and len(ready) == count:
            return
        time.sleep(2)
    raise RuntimeError("operator replica transition timed out")


def migrate(region, source, target):
    query(f"ADMIN migrate_region({region},{source},{target},120)")
    end = time.monotonic() + 180
    while time.monotonic() < end:
        rows = query(
            f"SELECT peer_id,status FROM information_schema.region_peers WHERE region_id={region}"
        )
        if len(rows) == 1 and int(rows[0][0]) == target and rows[0][1] == "ALIVE":
            return
        time.sleep(1)
    raise RuntimeError("native region migration timed out")


observer = LiveObserver(
    case,
    [*KUBE, "exec", "client", "--", "env", "FAULT_CASE=" + case, "client", "watch"],
    root,
)

log = (root / (case + "-load.log")).open("w")
load = subprocess.Popen(
    [*KUBE, "exec", "client", "--", "env", "FAULT_CASE=" + case, "client", "churn"],
    stdout=log,
    stderr=subprocess.STDOUT,
)
try:
    wait_progress(case, load)
    observer.progress()
    observer.mark("operation")
    begin = time.monotonic()
    if mode == "out":
        original = active_peers()
        if len(original) != 3:
            raise RuntimeError("expected three datanodes before scale-out")
        patch(5)
        end = time.monotonic() + 120
        while time.monotonic() < end:
            current = active_peers()
            added = sorted(current - original)
            if len(added) == 2:
                break
            time.sleep(2)
        else:
            raise RuntimeError("new datanode IDs not registered")
        regions = query(
            "SELECT region_id,peer_id FROM information_schema.region_peers WHERE table_name='fleet_load' ORDER BY region_id"
        )
        if len(regions) != 3:
            raise RuntimeError("unexpected workload partition count")
        for row, target in zip(regions[:2], added):
            migrate(int(row[0]), int(row[1]), target)
        (root / "scale-plan.json").write_text(
            json.dumps({"original": sorted(original), "added": added}) + "\n"
        )
    else:
        plan = json.loads((root / "scale-plan.json").read_text())
        original = plan["original"]
        added = set(plan["added"])
        regions = query("SELECT region_id,peer_id FROM information_schema.region_peers")
        for region, source in regions:
            if int(source) in added:
                migrate(int(region), int(source), original[int(region) % len(original)])
        if any(
            int(r[1]) in added
            for r in query(
                "SELECT region_id,peer_id FROM information_schema.region_peers"
            )
        ):
            raise RuntimeError("new nodes still own regions")
        patch(3)
    elapsed = time.monotonic() - begin
    wait_baseline()
    wait_progress(case, load, after=now())
    observer.mark("after")
    observer.progress()
    (root / ("stop-" + case)).touch()
    if load.wait(timeout=150) != 0:
        raise RuntimeError("producer failed")
    observations = observer.finish()
    kubectl(
        "exec", "client", "--", "env", "FAULT_CASE=" + case, "client", "verify-fault"
    )
    kubectl("exec", "client", "--", "client", "verify")
    (root / (case + "-result.json")).write_text(
        json.dumps(
            {
                "migration_seconds": elapsed,
                "observations": observations,
                "regions": query(
                    "SELECT table_name,region_id,peer_id,status FROM information_schema.region_peers"
                ),
                "reconciliation": json.loads(
                    (root / ("fault-" + case + "-reconciliation.json")).read_text()
                ),
            },
            indent=2,
        )
        + "\n"
    )
finally:
    observer.close()
    if load.poll() is None:
        (root / ("stop-" + case)).touch()
        try:
            load.wait(timeout=150)
        except subprocess.TimeoutExpired:
            load.terminate()
            load.wait(timeout=20)
    log.close()
