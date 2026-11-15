"""Scoped operator pod crash / persistent local cache replacement under writes."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from live_observer import LiveObserver

from runtime import wait_progress

KUBE = ["kubectl", "--context", "kind-greptime-operator", "--namespace", "greptime-lab"]
ROOT = Path("results")
mode = sys.argv[1]
if mode not in ("process", "disk"):
    raise ValueError(mode)
case = "k8s-" + mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def kubectl(*args, capture=False):
    return subprocess.run(
        [*KUBE, *args], check=True, text=True, capture_output=capture
    ).stdout


def query(sql):
    return json.loads(
        kubectl("exec", "client", "--", "client", "sql", sql, capture=True)
    )


def wait_owner(region, peer):
    end = time.monotonic() + 180
    while time.monotonic() < end:
        rows = query(
            f"SELECT peer_id,status FROM information_schema.region_peers WHERE region_id={region}"
        )
        if len(rows) == 1 and int(rows[0][0]) == peer and rows[0][1] == "ALIVE":
            return
        time.sleep(1)
    raise RuntimeError("native region migration timed out")


def wait_pod(name, old_uid):
    end = time.monotonic() + 300
    while time.monotonic() < end:
        value = json.loads(
            kubectl(
                "get", "pod", name, "--ignore-not-found", "-o", "json", capture=True
            )
            or "null"
        )
        if (
            value
            and value["metadata"]["uid"] != old_uid
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in value["status"].get("conditions", [])
            )
        ):
            return value
        time.sleep(2)
    raise RuntimeError("operator replacement pod did not become ready")


def wait_stopped(old_pod):
    node = old_pod["spec"]["nodeName"]
    info = json.loads(subprocess.check_output(["docker", "inspect", node], text=True))[
        0
    ]
    if info["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "greptime-operator":
        raise RuntimeError("refusing foreign kind worker")
    old_ids = {
        c["containerID"].split("://", 1)[1]
        for c in old_pod["status"].get("containerStatuses", [])
    }
    if not old_ids:
        raise RuntimeError("old pod has no container identity")
    end = time.monotonic() + 90
    while time.monotonic() < end:
        containers = json.loads(
            subprocess.check_output(
                ["docker", "exec", node, "crictl", "ps", "-a", "-o", "json"], text=True
            )
        )["containers"]
        if not any(
            c["id"] in old_ids and c["state"] != "CONTAINER_EXITED" for c in containers
        ):
            return
        time.sleep(1)
    raise RuntimeError(
        "old process still running after Pod deletion; refusing disk replacement"
    )


pods = json.loads(kubectl("get", "pods", "-o", "json", capture=True))["items"]
name = "telemetry-datanode-2"
pod = next(p for p in pods if p["metadata"]["name"] == name)
if not any(
    r["kind"] == "StatefulSet" and r["name"] == "telemetry-datanode"
    for r in pod["metadata"].get("ownerReferences", [])
):
    raise RuntimeError("pod ownership mismatch")
peer_rows = query(
    "SELECT peer_id,peer_addr FROM information_schema.cluster_info WHERE peer_type='DATANODE'"
)
target = next(
    int(row[0]) for row in peer_rows if row[1].split(":")[0] == pod["status"]["podIP"]
)
regions = query(
    "SELECT region_id,peer_id FROM information_schema.region_peers WHERE table_name='fleet_load' ORDER BY region_id"
)
region = int(regions[0][0])
if int(regions[0][1]) != target:
    query(f"ADMIN migrate_region({region},{int(regions[0][1])},{target},120)")
    wait_owner(region, target)
claims = [
    v["persistentVolumeClaim"]["claimName"]
    for v in pod["spec"]["volumes"]
    if "persistentVolumeClaim" in v
]
if mode == "disk" and len(claims) != 1:
    raise RuntimeError("expected exactly one owned local-data PVC")
before = {
    p["metadata"]["name"]: p["metadata"]["uid"]
    for p in pods
    if p["metadata"]["name"] != name
}
observer = LiveObserver(
    case,
    [*KUBE, "exec", "client", "--", "env", "FAULT_CASE=" + case, "client", "watch"],
    ROOT,
)

log = (ROOT / (case + "-load.log")).open("w")
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
    injected = datetime.now(timezone.utc)
    if mode == "disk":
        # Stop reconciliation at two replicas before deleting the owned cache
        # claim, so no newly scheduled pod can still be using that disk.
        kubectl(
            "patch",
            "greptimedbcluster",
            "telemetry",
            "--type",
            "merge",
            "-p",
            json.dumps({"spec": {"datanode": {"replicas": 2}}}),
        )
    kubectl(
        "delete", "pod", name, "--grace-period=0", "--force", "--ignore-not-found=true"
    )
    wait_stopped(pod)
    if mode == "disk":
        kubectl("wait", "--for=delete", "pod/" + name, "--timeout=120s")
        # This is cache loss with remote WAL/S3 retained, not destruction of the
        # object-store copies. Claim name comes from the validated owned pod.
        kubectl("delete", "pvc", claims[0], "--wait=true", "--timeout=120s")
        time.sleep(30)
        end = time.monotonic() + 300
        while time.monotonic() < end:
            rows = query(
                f"SELECT peer_id,status FROM information_schema.region_peers WHERE region_id={region}"
            )
            if len(rows) == 1 and int(rows[0][0]) != target and rows[0][1] == "ALIVE":
                break
            time.sleep(2)
        else:
            raise RuntimeError("region did not fail over while cache was absent")
        kubectl(
            "patch",
            "greptimedbcluster",
            "telemetry",
            "--type",
            "merge",
            "-p",
            json.dumps({"spec": {"datanode": {"replicas": 3}}}),
        )
    replacement = wait_pod(name, pod["metadata"]["uid"])
    if mode == "disk":
        # Prove the new local disk can own and read existing data again.
        peer_rows = query(
            "SELECT peer_id,peer_addr FROM information_schema.cluster_info WHERE peer_type='DATANODE'"
        )
        new_peer = next(
            int(row[0])
            for row in peer_rows
            if row[1].split(":")[0] == replacement["status"]["podIP"]
        )
        source = int(
            query(
                f"SELECT peer_id FROM information_schema.region_peers WHERE region_id={region}"
            )[0][0]
        )
        query(f"ADMIN migrate_region({region},{source},{new_peer},120)")
        wait_owner(region, new_peer)
    recovered = time.monotonic() - begin
    wait_progress(case, load, after=datetime.now(timezone.utc))
    observer.mark("after")
    observer.progress()
    (ROOT / ("stop-" + case)).touch()
    if load.wait(timeout=150) != 0:
        raise RuntimeError("producer failed")
    observations = observer.finish()
    kubectl(
        "exec", "client", "--", "env", "FAULT_CASE=" + case, "client", "verify-fault"
    )
    kubectl("exec", "client", "--", "client", "verify")
    current = json.loads(kubectl("get", "pods", "-o", "json", capture=True))["items"]
    after = {
        p["metadata"]["name"]: p["metadata"]["uid"]
        for p in current
        if p["metadata"]["name"] in before
    }
    if before != after:
        raise RuntimeError("an unrelated surviving pod changed UID")
    records = [
        json.loads(line)
        for line in (ROOT / ("fault-" + case + ".jsonl")).read_text().splitlines()
    ]
    acks = [
        datetime.fromisoformat(r["at"].replace("Z", "+00:00"))
        for r in records
        if r["stage"] == "ack"
    ]
    output = {
        "case": mode,
        "observations": observations,
        "scope": "controlled local PVC loss, remote WAL/S3 retained"
        if mode == "disk"
        else "forced owning-datanode pod deletion",
        "recovery_seconds": recovered,
        "final_verified_seconds": time.monotonic() - begin,
        "first_ack_after_injection_seconds": next(
            ((t - injected).total_seconds() for t in acks if t >= injected), None
        ),
        "old_uid": pod["metadata"]["uid"],
        "new_uid": replacement["metadata"]["uid"],
        "survivors_unchanged": len(before),
        "reconciliation": json.loads(
            (ROOT / ("fault-" + case + "-reconciliation.json")).read_text()
        ),
    }
    (ROOT / (case + "-result.json")).write_text(json.dumps(output, indent=2) + "\n")
finally:
    observer.close()
    if mode == "disk":
        subprocess.run(
            [
                *KUBE,
                "patch",
                "greptimedbcluster",
                "telemetry",
                "--type",
                "merge",
                "-p",
                json.dumps({"spec": {"datanode": {"replicas": 3}}}),
            ],
            check=True,
        )
    if load.poll() is None:
        (ROOT / ("stop-" + case)).touch()
        try:
            load.wait(timeout=150)
        except subprocess.TimeoutExpired:
            load.terminate()
            load.wait(timeout=20)
    log.close()
