"""Operator CR operations, native repair and owned node/process/disk failures."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from live_observer import LiveObserver
from production_observations import timestamp

ROOT = Path("results")
os.environ["KUBECONFIG"] = str(ROOT.resolve() / "kubeconfig")
KUBE = ["kubectl", "--context", "kind-starrocks-operator", "-n", "starrocks-lab"]
mode = sys.argv[1]
MODES = (
    "ready",
    "scale-out",
    "scale-in",
    "scale-up",
    "scale-down",
    "rolling-restart",
    "failover",
    "chaos-fe-leader",
    "chaos-node",
    "chaos-operator",
    "disk-failure",
    "chaos-network",
    "chaos-write-quorum",
    "drain-test",
)
if mode not in MODES:
    raise ValueError(mode)
case = mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def run(*args, capture=False, timeout=300):
    result = subprocess.run(
        list(args), check=False, text=True, capture_output=capture, timeout=timeout
    )
    if result.returncode:
        if capture:
            with (ROOT / (case + "-commands.log")).open("a") as log:
                log.write(result.stdout + result.stderr)
            raise RuntimeError(f"{args}: {result.stderr[-4000:]}")
        result.check_returncode()
    return result.stdout


def kube(*args, capture=False):
    return run(*KUBE, *args, capture=capture)


def tool(action, *args, capture=False):
    return kube(
        "exec", "orders-workload", "--", "client", action, *args, capture=capture
    )


def metadata(query):
    return json.loads(tool("metadata", query, capture=True))


def execute(query):
    leader = next(r["IP"] for r in metadata("SHOW FRONTENDS") if r["Role"] == "LEADER")
    return kube(
        "exec",
        "orders-workload",
        "--",
        "env",
        "SR_FRONTENDS=" + leader,
        "client",
        "exec",
        "starrocks",
        query,
    )


def patch(spec):
    kube(
        "patch",
        "starrockscluster",
        "orders",
        "--type=merge",
        "-p",
        json.dumps({"spec": spec}),
    )


def wait(predicate, description, timeout=300):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(2)
    raise RuntimeError("timed out: " + description)


def pods():
    return json.loads(kube("get", "pods", "-o", "json", capture=True))["items"]


def be_count():
    return int(
        json.loads(
            kube("get", "starrockscluster", "orders", "-o", "json", capture=True)
        )["spec"]["starRocksBeSpec"]["replicas"]
    )


def native_ready(count=None, physical=True):
    count = count if count is not None else be_count()

    def healthy():
        try:
            front, back = metadata("SHOW FRONTENDS"), metadata("SHOW BACKENDS")
        except RuntimeError:
            return False
        return (
            len(front) == 3
            and all(r["Alive"] == "true" for r in front)
            and sum(r["Role"] == "LEADER" for r in front) == 1
            and len(back) == count
            and all(r["Alive"] == "true" for r in back)
        )

    wait(healthy, "native membership")
    if physical:

        def repaired():
            try:
                tool("replica-health", capture=True)
                # Heartbeats and direct BE HTTP checks can recover before FE
                # query routing removes a restarted backend from its blacklist.
                settings = metadata(
                    "SELECT BE_ID,NAME,VALUE FROM information_schema.be_configs WHERE NAME='sync_tablet_meta'"
                )
                return len(settings) == count and all(
                    row["VALUE"] == "true" for row in settings
                )
            except RuntimeError:
                return False

        wait(repaired, "physical tablet copies and backend query readiness", timeout=600)


def rollout(role, expected):
    def changed():
        template = json.loads(
            kube("get", "statefulset/orders-" + role, "-o", "json", capture=True)
        )["spec"]["template"]
        return expected(template)

    wait(changed, "operator updated " + role + " pod template")
    kube("rollout", "status", "statefulset/orders-" + role, "--timeout=600s")


def journal():
    path = ROOT / ("fault-" + case + ".jsonl")
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(after):
    def active():
        if load.poll() is not None:
            raise RuntimeError("writer exited before recovery")
        return (
            sum(r["stage"] == "ack" and timestamp(r["at"]) > after for r in journal())
            >= 3
        )

    wait(active, "fresh acknowledged writes")
    observer.progress()


def owned_node(name):
    value = json.loads(run("docker", "inspect", name, capture=True))[0]
    if value["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "starrocks-operator":
        raise RuntimeError("refusing foreign node")
    return value


def unchanged_survivors(before):
    current = {p["metadata"]["name"]: p for p in pods()}
    checked = {}
    for old in before:
        name = old["metadata"]["name"]
        if not name.startswith(("orders-fe-", "orders-be-")):
            continue
        if mode in (
            "scale-up",
            "scale-down",
            "rolling-restart",
            "chaos-operator",
        ) and name.startswith("orders-be-"):
            continue
        if mode == "scale-in" and name in ("orders-be-3", "orders-be-4"):
            continue
        if mode == "chaos-write-quorum" and name in ("orders-be-1", "orders-be-2"):
            continue
        if mode in ("chaos-node", "drain-test") and old["spec"][
            "nodeName"
        ] == details.get("target_node"):
            continue
        if mode in (
            "failover",
            "chaos-fe-leader",
            "disk-failure",
        ) and name == details.get("target_pod"):
            continue
        new = current.get(name)
        previous = [c["containerID"] for c in old["status"]["containerStatuses"]]
        if (
            new is None
            or new["metadata"]["uid"] != old["metadata"]["uid"]
            or [c["containerID"] for c in new["status"]["containerStatuses"]]
            != previous
        ):
            raise RuntimeError("unrelated database member restarted: " + name)
        checked[name] = {"pod_uid": old["metadata"]["uid"], "container_ids": previous}
    return checked


if mode == "ready":
    native_ready(3, physical=False)
    for role in ("fe", "be"):
        members = [
            p
            for p in pods()
            if p["metadata"]["name"].startswith("orders-" + role + "-")
        ]
        if len(members) != 3 or len({p["spec"]["nodeName"] for p in members}) != 3:
            raise RuntimeError(
                "expected one " + role + " per worker before loading data"
            )
    raise SystemExit(0)
native_ready()
observer = LiveObserver(
    case,
    [
        *KUBE,
        "exec",
        "orders-workload",
        "--",
        "env",
        "FAULT_CASE=" + case,
        "client",
        "watch",
    ],
    ROOT,
)
log = (ROOT / (case + "-writer.log")).open("w")
load = subprocess.Popen(
    [
        *KUBE,
        "exec",
        "orders-workload",
        "--",
        "env",
        "FAULT_CASE=" + case,
        "client",
        "churn",
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
)
stopped_node, operator_stopped = None, False
disk_scaled_down = False
network_rules, cordoned_node, reduced_quorum = [], None, False
details = {}
try:
    progress(observer.markers[0][1])
    before = pods()
    (ROOT / (case + "-before-pods.json")).write_text(
        json.dumps({"items": before}, indent=2) + "\n"
    )
    began = time.monotonic()
    observer.mark("operation")
    if mode == "scale-out":
        if be_count() != 3:
            raise RuntimeError("expected three backends")
        patch({"starRocksBeSpec": {"replicas": 5}})
        wait(
            lambda: len(
                [p for p in pods() if p["metadata"]["name"].startswith("orders-be-")]
            )
            == 5,
            "five BE pods",
        )
        kube("rollout", "status", "statefulset/orders-be", "--timeout=600s")
        native_ready(5)

        def owns_orders():
            backends = metadata("SHOW BACKENDS")
            tablets = metadata("SHOW TABLET FROM orders_lab.orders")
            return len(backends) == 5 and {r["BackendId"] for r in backends} <= {
                r["BackendId"] for r in tablets
            }

        wait(owns_orders, "all five backends own workload order tablets", timeout=600)
        details["orders_replica_placement"] = metadata(
            "SHOW TABLET FROM orders_lab.orders"
        )
    elif mode == "scale-in":
        if be_count() != 5:
            raise RuntimeError("scale-out must run first")
        execute('ADMIN SET FRONTEND CONFIG ("drop_backend_after_decommission"="false")')
        for ordinal in (4, 3):
            selected = [
                r
                for r in metadata("SHOW BACKENDS")
                if r["IP"].startswith(f"orders-be-{ordinal}.")
            ]
            if len(selected) != 1:
                raise RuntimeError("could not identify highest backend ordinal")
            address = selected[0]["IP"] + ":9050"
            execute('ALTER SYSTEM DECOMMISSION BACKEND "' + address + '"')
            wait(
                lambda: any(
                    r["IP"] == selected[0]["IP"]
                    and int(r["TabletNum"]) == 0
                    and r["SystemDecommissioned"] == "true"
                    for r in metadata("SHOW BACKENDS")
                ),
                "native backend drain",
                timeout=600,
            )
            execute('ALTER SYSTEM DROP BACKEND "' + address + '"')
            patch({"starRocksBeSpec": {"replicas": ordinal}})
            kube("wait", "--for=delete", f"pod/orders-be-{ordinal}", "--timeout=180s")
        execute('ADMIN SET FRONTEND CONFIG ("drop_backend_after_decommission"="true")')
    elif mode in ("scale-up", "scale-down"):
        limits = (
            {"cpu": "1500m", "memory": "2Gi"}
            if mode == "scale-up"
            else {"cpu": "1", "memory": "1536Mi"}
        )
        patch({"starRocksBeSpec": {"limits": limits}})
        rollout(
            "be", lambda t: t["spec"]["containers"][0]["resources"]["limits"] == limits
        )
        details["effective_resources"] = [
            p["spec"]["containers"][0]["resources"]
            for p in pods()
            if p["metadata"]["name"].startswith("orders-be-")
        ]
    elif mode == "rolling-restart":
        old = {
            p["metadata"]["name"]: p["metadata"]["uid"]
            for p in before
            if p["metadata"]["name"].startswith("orders-be-")
        }
        patch({"starRocksBeSpec": {"annotations": {"lab.starrocks/restart": case}}})
        rollout(
            "be",
            lambda t: t["metadata"].get("annotations", {}).get("lab.starrocks/restart")
            == case,
        )
        if any(
            p["metadata"]["uid"] == old.get(p["metadata"]["name"])
            for p in pods()
            if p["metadata"]["name"] in old
        ):
            raise RuntimeError("rolling change did not replace all three backends")
    elif mode == "chaos-operator":
        kube("scale", "deployment/orders-operator", "--replicas=0")
        operator_stopped = True
        kube(
            "wait", "--for=delete", "pod", "-l", "app=orders-operator", "--timeout=120s"
        )
        patch({"starRocksBeSpec": {"annotations": {"lab.starrocks/reconcile": case}}})
        time.sleep(20)
        template = json.loads(
            kube("get", "statefulset/orders-be", "-o", "json", capture=True)
        )["spec"]["template"]
        if (
            template["metadata"].get("annotations", {}).get("lab.starrocks/reconcile")
            == case
        ):
            raise RuntimeError("operator change reconciled during controller outage")
        progress(observer.markers[-1][1])
        observer.mark("recovery")
        kube("scale", "deployment/orders-operator", "--replicas=1")
        operator_stopped = False
        rollout(
            "be",
            lambda t: t["metadata"]
            .get("annotations", {})
            .get("lab.starrocks/reconcile")
            == case,
        )
    elif mode == "chaos-write-quorum":
        patch({"starRocksBeSpec": {"replicas": 1}})
        reduced_quorum = True
        for ordinal in (2, 1):
            kube("wait", "--for=delete", f"pod/orders-be-{ordinal}", "--timeout=180s")
        fenced = datetime.now(timezone.utc)
        time.sleep(30)
        late_acks = [
            r
            for r in journal()
            if r["stage"] == "ack"
            and timestamp(r["at"]) > fenced + timedelta(seconds=10)
        ]
        if late_acks:
            raise RuntimeError(
                "writes acknowledged without the configured two-copy quorum"
            )
        if load.poll() is not None:
            raise RuntimeError("writer stopped during quorum loss")
        details["scope"] = (
            "two BE Pods removed through CR; RF3 and retained PVCs unchanged"
        )
        observer.mark("recovery")
        patch({"starRocksBeSpec": {"replicas": 3}})
        reduced_quorum = False
        kube("rollout", "status", "statefulset/orders-be", "--timeout=600s")
    elif mode == "chaos-network":
        selected = next(p for p in before if p["metadata"]["name"] == "orders-be-2")
        node = selected["spec"]["nodeName"]
        details["target_node"] = node
        owned_node(node)
        ports = "8030,8040,8060,9010,9020,9030,9050,9060"
        for direction in ("--dports", "--sports"):
            rule = [
                "FORWARD",
                "-p",
                "tcp",
                "-m",
                "multiport",
                direction,
                ports,
                "-m",
                "comment",
                "--comment",
                case,
                "-j",
                "DROP",
            ]
            run("docker", "exec", node, "iptables", "-I", *rule)
            network_rules.append((node, rule))
        time.sleep(30)
        counters = run(
            "docker", "exec", node, "iptables", "-L", "FORWARD", "-nvx", capture=True
        )
        dropped = [
            line
            for line in counters.splitlines()
            if case in line and int(line.split()[0]) > 0
        ]
        if not dropped:
            raise RuntimeError("network rules did not drop database packets")
        details["drop_counters"] = dropped
        progress(observer.markers[-1][1])
        observer.mark("recovery")
        for node, rule in network_rules:
            run("docker", "exec", node, "iptables", "-D", *rule)
        network_rules.clear()
    elif mode == "drain-test":
        selected = next(p for p in before if p["metadata"]["name"] == "orders-be-2")
        node = selected["spec"]["nodeName"]
        details["target_node"] = node
        owned_node(node)
        if node.endswith("control-plane"):
            raise RuntimeError("refusing control-plane drain")
        cordoned_node = node
        run(
            "kubectl",
            "--context",
            "kind-starrocks-operator",
            "drain",
            node,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--timeout=120s",
        )

        def budgets_block():
            budgets = json.loads(kube("get", "pdb", "-o", "json", capture=True))[
                "items"
            ]
            return len(budgets) == 2 and all(
                b.get("status", {}).get("disruptionsAllowed") == 0 for b in budgets
            )

        wait(budgets_block, "PDB prevents second voluntary disruption")
        other = next(
            p
            for p in pods()
            if p["metadata"]["name"].startswith("orders-fe-")
            and p["spec"].get("nodeName") not in (None, node)
            and any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in p["status"].get("conditions", [])
            )
        )
        name = other["metadata"]["name"]
        request = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {"name": name, "namespace": "starrocks-lab"},
        }
        response = subprocess.run(
            [
                *KUBE,
                "create",
                "--raw",
                f"/api/v1/namespaces/starrocks-lab/pods/{name}/eviction",
                "-f",
                "-",
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=30,
        )
        details["second_eviction"] = response.stdout + response.stderr
        if (
            response.returncode == 0
            or "disruption budget" not in response.stderr.lower()
        ):
            raise RuntimeError("unsafe second eviction was not rejected by the PDB")
        progress(observer.markers[-1][1])
        observer.mark("recovery")
        run("kubectl", "--context", "kind-starrocks-operator", "uncordon", node)
        cordoned_node = None
        kube("rollout", "status", "statefulset/orders-fe", "--timeout=600s")
        kube("rollout", "status", "statefulset/orders-be", "--timeout=600s")
    else:
        name = "orders-be-2"
        if mode == "chaos-fe-leader":
            address = next(
                r["IP"] for r in metadata("SHOW FRONTENDS") if r["Role"] == "LEADER"
            )
            name = address.split(".")[0]
        selected = next(p for p in before if p["metadata"]["name"] == name)
        node = selected["spec"]["nodeName"]
        details["target_node"] = node
        owned_node(node)
        cid = selected["status"]["containerStatuses"][0]["containerID"].removeprefix(
            "containerd://"
        )
        details.update(
            target_pod=name,
            old_uid=selected["metadata"]["uid"],
            old_container=cid,
            target_node=node,
        )
        if mode == "chaos-node":
            if node.endswith("control-plane"):
                raise RuntimeError("refusing control-plane outage")
            run("docker", "kill", "--signal", "SIGKILL", node)
            stopped_node = node

            def unavailable():
                value = json.loads(
                    run(
                        "kubectl",
                        "--context",
                        "kind-starrocks-operator",
                        "get",
                        "node",
                        node,
                        "-o",
                        "json",
                        capture=True,
                    )
                )
                condition = next(
                    c for c in value["status"]["conditions"] if c["type"] == "Ready"
                )
                if condition["status"] in ("False", "Unknown"):
                    details["unavailable_node_condition"] = condition
                    return True
                return False

            wait(unavailable, "owned worker unavailable", timeout=120)
            time.sleep(30)
            progress(observer.markers[-1][1])
            observer.mark("recovery")
            run("docker", "start", node)
            stopped_node = None
            run(
                "kubectl",
                "--context",
                "kind-starrocks-operator",
                "wait",
                "--for=condition=Ready",
                "node/" + node,
                "--timeout=180s",
            )
        elif mode == "disk-failure":
            claims = [
                v["persistentVolumeClaim"]["claimName"]
                for v in selected["spec"]["volumes"]
                if "persistentVolumeClaim" in v
            ]
            if len(claims) != 1:
                raise RuntimeError("expected one owned BE data claim")
            claim = json.loads(
                kube("get", "pvc", claims[0], "-o", "json", capture=True)
            )
            stateful = json.loads(
                kube("get", "statefulset/orders-be", "-o", "json", capture=True)
            )
            owner = json.loads(
                kube("get", "starrockscluster/orders", "-o", "json", capture=True)
            )
            if not any(
                r.get("controller") and r["uid"] == stateful["metadata"]["uid"]
                for r in selected["metadata"].get("ownerReferences", [])
            ):
                raise RuntimeError("BE Pod is not controlled by the owned StatefulSet")
            if not any(
                r["uid"] == owner["metadata"]["uid"]
                for r in stateful["metadata"].get("ownerReferences", [])
            ):
                raise RuntimeError("StatefulSet is not owned by this StarRocks cluster")
            expected = {
                v["metadata"]["name"] + "-" + name
                for v in stateful["spec"]["volumeClaimTemplates"]
            }
            if (
                claims[0] not in expected
                or claim["metadata"]["namespace"] != "starrocks-lab"
            ):
                raise RuntimeError("claim does not belong to this BE ordinal")
            details["old_claim_uid"] = claim["metadata"]["uid"]
            patch({"starRocksBeSpec": {"replicas": 2}})
            disk_scaled_down = True
            kube("wait", "--for=delete", "pod/" + name, "--timeout=180s")
            kube("delete", "pvc", claims[0], "--wait=true", "--timeout=120s")
            time.sleep(30)
            progress(observer.markers[-1][1])
            observer.mark("recovery")
            patch({"starRocksBeSpec": {"replicas": 3}})
            disk_scaled_down = False
            wait(
                lambda: any(p["metadata"]["name"] == name for p in pods()),
                "empty-disk replacement pod",
            )
            kube("rollout", "status", "statefulset/orders-be", "--timeout=600s")
            new_claim = json.loads(
                kube("get", "pvc", claims[0], "-o", "json", capture=True)
            )
            if new_claim["metadata"]["uid"] == claim["metadata"]["uid"]:
                raise RuntimeError("claim was not replaced")
            details["new_claim_uid"] = new_claim["metadata"]["uid"]
            for table in ("orders", "customers", "recovery"):
                execute("ADMIN REPAIR TABLE orders_lab." + table)
        else:
            run(
                "docker",
                "exec",
                node,
                "ctr",
                "-n",
                "k8s.io",
                "tasks",
                "kill",
                "--signal",
                "SIGKILL",
                cid,
            )

            def exited():
                value = subprocess.run(
                    ["docker", "exec", node, "crictl", "inspect", cid],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                return (
                    value.returncode != 0
                    or json.loads(value.stdout)["status"]["state"]
                    != "CONTAINER_RUNNING"
                )

            wait(exited, "old runtime process exited", timeout=60)
            observer.mark("recovery")
    native_ready()
    observer.mark("after")
    progress(observer.markers[-1][1])
    (ROOT / ("stop-" + case)).touch()
    if load.wait(timeout=60):
        raise RuntimeError("writer failed")
    observations = observer.finish()
    tool("verify")
    details["unchanged_survivors"] = unchanged_survivors(before)
    details.update(
        case=case,
        native_recovery_and_verification_seconds=time.monotonic() - began,
        observations=observations,
        frontends=metadata("SHOW FRONTENDS"),
        backends=metadata("SHOW BACKENDS"),
        reconciliation=json.loads((ROOT / "recovery-history.json").read_text()),
    )
    (ROOT / (case + "-result.json")).write_text(json.dumps(details, indent=2) + "\n")
except Exception as error:
    (ROOT / (case + "-failure.json")).write_text(
        json.dumps({"case": case, "error": str(error), "details": details}, indent=2)
        + "\n"
    )
    raise
finally:
    for node, rule in network_rules:
        run("docker", "exec", node, "iptables", "-D", *rule)
    if cordoned_node:
        run(
            "kubectl", "--context", "kind-starrocks-operator", "uncordon", cordoned_node
        )
    if reduced_quorum:
        patch({"starRocksBeSpec": {"replicas": 3}})
    if disk_scaled_down:
        patch({"starRocksBeSpec": {"replicas": 3}})
    if stopped_node:
        run("docker", "start", stopped_node)
    if operator_stopped:
        kube("scale", "deployment/orders-operator", "--replicas=1")
    (ROOT / ("stop-" + case)).touch()
    observer.close()
    try:
        load.wait(timeout=30)
    except subprocess.TimeoutExpired:
        load.terminate()
        load.wait(timeout=10)
    log.close()
    with (ROOT / (case + "-pods.json")).open("w") as output:
        subprocess.run(
            [*KUBE, "get", "pods", "-o", "json"], stdout=output, check=False, timeout=30
        )
