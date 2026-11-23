"""Operator reconciliation, resizing, rolling maintenance and worker recovery."""

from datetime import datetime
import json
import subprocess
import sys
import time

from live_observer import LiveObserver

from runtime import (
    KUBE,
    ROOT,
    kubectl,
    now,
    pods,
    query,
    records,
    wait_cluster,
    wait_progress,
)

mode = sys.argv[1]
if mode not in ("node", "operator", "scale-up", "scale-down", "rolling", "wal-quorum"):
    raise ValueError(mode)
case = "k8s-" + mode + "-" + now().strftime("%Y%m%dT%H%M%S")


def patch(spec):
    kubectl(
        "patch",
        "greptimedbcluster",
        "telemetry",
        "--type",
        "merge",
        "-p",
        json.dumps({"spec": spec}),
    )


def owned_node(name):
    value = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[
        0
    ]
    if value["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "greptime-operator":
        raise RuntimeError("refusing foreign worker")
    return value


wait_cluster()
before = pods()
operator = json.loads(kubectl("get", "deployments", "-o", "json", capture=True))[
    "items"
]
operator = [
    d for d in operator if d["metadata"]["name"].startswith("greptime-operator-")
]
if len(operator) != 1 or operator[0]["spec"]["replicas"] != 1:
    raise RuntimeError("expected one owned operator deployment")
operator_name = operator[0]["metadata"]["name"]
worker = "greptime-operator-worker3"
node_info = None
details = {}
wal_targets = []
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
    injected = now()
    if mode == "wal-quorum":
        broker_names = ["greptime-kind-support-wal" + str(i) + "-1" for i in range(3)]
        old_brokers = json.loads(
            subprocess.check_output(["docker", "inspect", *broker_names], text=True)
        )
        if any(
            b["Config"]["Labels"].get("com.docker.compose.project")
            != "greptime-kind-support"
            for b in old_brokers
        ):
            raise RuntimeError("refusing foreign WAL cluster")
        old_addresses = {
            b["Name"]: b["NetworkSettings"]["Networks"][
                "greptime-kind-support_default"
            ]["IPAddress"]
            for b in old_brokers
        }
        details["wal_addresses_before"] = old_addresses
        for service in ("wal1", "wal2"):
            name = "greptime-kind-support-" + service + "-1"
            info = json.loads(
                subprocess.check_output(["docker", "inspect", name], text=True)
            )[0]
            labels = info["Config"]["Labels"]
            if (
                labels.get("com.docker.compose.project") != "greptime-kind-support"
                or labels.get("com.docker.compose.service") != service
            ):
                raise RuntimeError("refusing foreign WAL broker")
            wal_targets.append(name)
        subprocess.run(
            ["docker", "stop", "--timeout", "0", *wal_targets], check=True, timeout=60
        )
        stopped_at = now()
        time.sleep(30)
        # Allow in-flight pre-fault completions ten seconds, then demand that a
        # minority cannot acknowledge new remote-WAL commits.
        minority_acks = [
            r
            for r in records(case)
            if r["stage"] == "ack"
            and datetime.fromisoformat(r["at"].replace("Z", "+00:00")).timestamp()
            > stopped_at.timestamp() + 10
        ]
        details["acknowledged_rows_after_10s_grace_with_two_brokers_down"] = sum(
            r["count"] for r in minority_acks
        )
        if minority_acks:
            raise RuntimeError("minority WAL unexpectedly acknowledged writes")
        if load.poll() is not None:
            raise RuntimeError("producer exited during quorum loss")
        subprocess.run(["docker", "start", *wal_targets], check=True, timeout=60)
        new_brokers = json.loads(
            subprocess.check_output(["docker", "inspect", *broker_names], text=True)
        )
        new_addresses = {
            b["Name"]: b["NetworkSettings"]["Networks"][
                "greptime-kind-support_default"
            ]["IPAddress"]
            for b in new_brokers
        }
        details["wal_addresses_after_restart"] = new_addresses
        # External EndpointSlices contain concrete Docker IPs, not a watched
        # service discovery backend. A multi-container restart can swap IPs.
        # Refresh the bridge explicitly and clear the retained broker's cached
        # peer connections before waiting for native quorum/replica health.
        subprocess.run(
            [sys.executable, 'scripts/config.py', "endpoints"], check=True, timeout=60
        )
        details["survivor_peer_connection_restart"] = old_addresses != new_addresses
        if old_addresses != new_addresses:
            subprocess.run(
                ["docker", "restart", "--timeout", "10", broker_names[0]],
                check=True,
                timeout=60,
            )
            subprocess.run(
                [sys.executable, 'scripts/config.py', "endpoints"], check=True, timeout=60
            )
        health = subprocess.run(
            [
                "docker",
                "exec",
                "greptime-kind-support-wal0-1",
                "rpk",
                "cluster",
                "health",
                "--exit-when-healthy",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=180,
        )
        details["native_wal_health_after_recovery"] = health.stdout
        details["scope"] = (
            "temporary loss of two of three replicated WAL brokers; retained disks"
        )
    elif mode == "node":
        node_info = owned_node(worker)
        affected = [p for p in before if p["spec"].get("nodeName") == worker]
        if not affected or any(p["metadata"]["name"] == "client" for p in affected):
            raise RuntimeError(
                "worker fault must affect database pods and retain the witness"
            )
        target = next(
            p
            for p in affected
            if p["metadata"]["name"].startswith("telemetry-datanode-")
        )
        peer = next(
            int(r[0])
            for r in query(
                "SELECT peer_id,peer_addr FROM information_schema.cluster_info WHERE peer_type='DATANODE'"
            )
            if r[1].split(":")[0] == target["status"]["podIP"]
        )
        regions = query(
            "SELECT region_id,peer_id FROM information_schema.region_peers WHERE table_name='fleet_load' ORDER BY region_id"
        )
        region, source = map(int, regions[0])
        if source != peer:
            query(f"ADMIN migrate_region({region},{source},{peer},120)")
            end = time.monotonic() + 180
            while time.monotonic() < end:
                row = query(
                    f"SELECT peer_id,status FROM information_schema.region_peers WHERE region_id={region}"
                )
                if row == [[peer, "ALIVE"]]:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("selected worker never owned the test region")
        injected = now()
        subprocess.run(
            ["docker", "stop", "--time", "0", worker], check=True, timeout=60
        )
        end = time.monotonic() + 120
        while time.monotonic() < end:
            node = json.loads(
                kubectl("get", "node", worker, "-o", "json", capture=True)
            )
            condition = next(
                c for c in node["status"]["conditions"] if c["type"] == "Ready"
            )
            if condition["status"] != "True":
                break
            time.sleep(2)
        else:
            raise RuntimeError("worker never became NotReady")
        details["node_ready_during_outage"] = condition
        details["regions_during_outage"] = query(
            "SELECT table_name,region_id,peer_id,status FROM information_schema.region_peers"
        )
        # Require writes after the node is confirmed unavailable, not only before.
        wait_progress(case, load, after=now())
        subprocess.run(["docker", "start", worker], check=True, timeout=60)
        kubectl("wait", "node/" + worker, "--for=condition=Ready", "--timeout=120s")
        restored_node = json.loads(
            kubectl("get", "node", worker, "-o", "json", capture=True)
        )
        internal = next(
            address["address"]
            for address in restored_node["status"]["addresses"]
            if address["type"] == "InternalIP"
        )
        expected_ip = owned_node(worker)["NetworkSettings"]["Networks"]["kind"][
            "IPAddress"
        ]
        details["restored_worker_internal_ip"] = internal
        details["expected_kind_network_ip"] = expected_ip
        if internal != expected_ip:
            raise RuntimeError(
                "worker restarted on its secondary network; refusing a false node-recovery pass"
            )

        after_node = owned_node(worker)
        if (
            after_node["Id"] != node_info["Id"]
            or after_node["State"]["StartedAt"] == node_info["State"]["StartedAt"]
        ):
            raise RuntimeError(
                "expected retained worker container with a new start time"
            )
        details.update(
            worker=worker,
            old_started_at=node_info["State"]["StartedAt"],
            new_started_at=after_node["State"]["StartedAt"],
        )
    elif mode == "operator":
        kubectl("scale", "deployment/" + operator_name, "--replicas=0")
        end = time.monotonic() + 90
        while time.monotonic() < end:
            if not any(
                p["metadata"]["name"].startswith(operator_name + "-") for p in pods()
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError("operator did not stop")
        stamp = case
        patch(
            {
                "frontend": {
                    "template": {"annotations": {"lab.greptime/reconcile": stamp}}
                }
            }
        )
        wait_progress(case, load, after=now())
        time.sleep(10)
        frontend = json.loads(
            kubectl(
                "get", "deployment", "telemetry-frontend", "-o", "json", capture=True
            )
        )
        if (
            frontend["spec"]["template"]["metadata"]
            .get("annotations", {})
            .get("lab.greptime/reconcile")
            == stamp
        ):
            raise RuntimeError("unexpected reconciliation while controller was stopped")
        kubectl("scale", "deployment/" + operator_name, "--replicas=1")
        kubectl("rollout", "status", "deployment/" + operator_name, "--timeout=120s")
        details["pending_annotation"] = stamp
    else:
        if mode == "rolling":
            patch(
                {
                    "datanode": {
                        "template": {"annotations": {"lab.greptime/restart": case}}
                    }
                }
            )
        else:
            resources = {
                "requests": {"cpu": "200m", "memory": "512Mi"},
                "limits": {"cpu": "1500m", "memory": "1536Mi"}
                if mode == "scale-up"
                else {"cpu": "1", "memory": "1Gi"},
            }
            if all(
                p["spec"]["containers"][0].get("resources") == resources
                for p in before
                if p["metadata"]["name"].startswith("telemetry-datanode-")
            ):
                raise RuntimeError(
                    "datanodes already have target resources; run the opposite resize first"
                )
            patch({"datanode": {"template": {"main": {"resources": resources}}}})
            details["requested_resources"] = resources
    wait_cluster()
    after = pods()
    if mode in ("scale-up", "scale-down"):
        datanodes = [
            p for p in after if p["metadata"]["name"].startswith("telemetry-datanode-")
        ]
        if len(datanodes) != 3 or any(
            p["spec"]["containers"][0]["resources"] != resources for p in datanodes
        ):
            raise RuntimeError("running datanode resources do not match the CR")
    if mode == "rolling":
        old = {p["metadata"]["name"]: p["metadata"]["uid"] for p in before}
        if any(
            p["metadata"]["uid"] == old[p["metadata"]["name"]]
            for p in after
            if p["metadata"]["name"].startswith("telemetry-datanode-")
        ):
            raise RuntimeError("rolling annotation did not replace every datanode")
    if mode == "operator":
        frontend = json.loads(
            kubectl(
                "get", "deployment", "telemetry-frontend", "-o", "json", capture=True
            )
        )
        if (
            frontend["spec"]["template"]["metadata"]
            .get("annotations", {})
            .get("lab.greptime/reconcile")
            != case
        ):
            raise RuntimeError("pending CR change was not reconciled")
    recovered = time.monotonic() - begin
    wait_progress(case, load, after=now())
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
    acks = [r for r in records(case) if r["stage"] == "ack"]
    details.update(
        case=case,
        observations=observations,
        recovery_seconds=recovered,
        final_verified_seconds=time.monotonic() - begin,
        acknowledged_rows=sum(r["count"] for r in acks),
        first_ack_after_injection_seconds=next(
            (now_ack - injected).total_seconds()
            for r in acks
            if (now_ack := datetime.fromisoformat(r["at"].replace("Z", "+00:00")))
            >= injected
        ),
        reconciliation=json.loads(
            (ROOT / ("fault-" + case + "-reconciliation.json")).read_text()
        ),
        regions=query(
            "SELECT table_name,region_id,peer_id,status FROM information_schema.region_peers"
        ),
    )
    (ROOT / (case + "-result.json")).write_text(json.dumps(details, indent=2) + "\n")
except Exception as error:
    (ROOT / (case + "-failure.json")).write_text(
        json.dumps(
            {"case": case, "status": "failed", "error": str(error), "details": details},
            indent=2,
        )
        + "\n"
    )
    raise
finally:
    observer.close()
    if wal_targets:
        subprocess.run(["docker", "start", *wal_targets], check=True, timeout=60)
    if mode == "node" and node_info is not None:
        subprocess.run(["docker", "start", worker], check=True, timeout=60)
    if mode == "operator":
        kubectl("scale", "deployment/" + operator_name, "--replicas=1")
    if load.poll() is None:
        (ROOT / ("stop-" + case)).touch()
        try:
            load.wait(timeout=150)
        except subprocess.TimeoutExpired:
            load.terminate()
            load.wait(timeout=20)
    log.close()
