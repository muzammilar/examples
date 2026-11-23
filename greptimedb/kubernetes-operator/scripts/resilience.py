"""Declarative disruption budgets, real peer partitions, cgroup pressure and upgrade."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
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
    ready,
    wait_cluster,
    wait_progress,
)

mode = sys.argv[1]
if mode not in ("pdb", "drain", "partition", "oom", "cpu", "upgrade"):
    raise ValueError(mode)
case = "k8s-" + mode + "-" + now().strftime("%Y%m%dT%H%M%S")


def apply(objects):
    subprocess.run(
        [*KUBE, "apply", "-f", "-"],
        input=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}),
        text=True,
        check=True,
        timeout=60,
    )


def disruption_budgets():
    objects = []
    for kind, name, minimum in (
        ("statefulset", "telemetry-datanode", 2),
        ("deployment", "telemetry-meta", 2),
        ("deployment", "telemetry-frontend", 1),
    ):
        controller = json.loads(kubectl("get", kind, name, "-o", "json", capture=True))
        objects.append(
            {
                "apiVersion": "policy/v1",
                "kind": "PodDisruptionBudget",
                "metadata": {"name": name, "namespace": "greptime-lab"},
                "spec": {
                    "minAvailable": minimum,
                    "selector": controller["spec"]["selector"],
                },
            }
        )
    apply(objects)


if mode == "pdb":
    disruption_budgets()
    raise SystemExit(0)


def run(*args, capture=False, timeout=180):
    return subprocess.run(
        list(args), text=True, capture_output=capture, timeout=timeout, check=True
    ).stdout


def owned_node(name):
    node = json.loads(run("docker", "inspect", name, capture=True))[0]
    if node["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "greptime-operator":
        raise RuntimeError("refusing foreign kind node")
    return node


def patch(spec):
    kubectl(
        "patch",
        "greptimedbcluster",
        "telemetry",
        "--type=merge",
        "-p",
        json.dumps({"spec": spec}),
    )


def identity(current):
    return {
        pod["metadata"]["name"]: {
            "uid": pod["metadata"]["uid"],
            "containers": [
                {"id": c["containerID"], "restarts": c["restartCount"]}
                for c in pod["status"].get("containerStatuses", [])
            ],
        }
        for pod in current
    }


def cpu_stat(pod):
    node = pod["spec"]["nodeName"]
    owned_node(node)
    cid = pod["status"]["containerStatuses"][0]["containerID"].split("://", 1)[1]
    info = json.loads(
        run("docker", "exec", node, "crictl", "inspect", cid, capture=True)
    )
    pid = int(info["info"]["pid"])
    membership = run(
        "docker", "exec", node, "cat", f"/proc/{pid}/cgroup", capture=True
    ).strip()
    hierarchy, controllers, path = membership.split(":", 2)
    if (
        hierarchy != "0"
        or controllers
        or not path.startswith(("/kubepods.slice/", "/kubelet.slice/kubelet-kubepods"))
        or pod["metadata"]["uid"].replace("-", "_") not in path
        or not path.endswith("/cri-containerd-" + cid + ".scope")
    ):
        raise RuntimeError("expected exact workload cgroup v2 identity")
    stat = run(
        "docker",
        "exec",
        node,
        "cat",
        "/sys/fs/cgroup" + path + "/cpu.stat",
        capture=True,
    )
    quota = run(
        "docker",
        "exec",
        node,
        "cat",
        "/sys/fs/cgroup" + path + "/cpu.max",
        capture=True,
    ).split()
    if (
        len(quota) != 2
        or quota[0] == "max"
        or abs(int(quota[0]) / int(quota[1]) - 0.1) > 0.00001
    ):
        raise RuntimeError("effective cgroup CPU quota is not 100m")
    return {
        "container_id": cid,
        "cgroup": path,
        "cpu_max": quota,
        "cpu_stat": {
            line.split()[0]: int(line.split()[1]) for line in stat.splitlines()
        },
    }


cr = wait_cluster()
before_pods = pods()
before = identity(before_pods)
original_resources = cr["spec"]["datanode"]["template"]["main"]["resources"]
restore_resources = False
cordoned = None
chain_node, chain = None, None
excluded = set()
proof = {}
reader = LiveObserver(
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
    reader.progress()
    reader.mark("operation")
    begin = time.monotonic()
    if mode == "drain":
        disruption_budgets()
        witness = next(
            p["spec"]["nodeName"]
            for p in before_pods
            if p["metadata"]["name"] == "client"
        )
        candidates = []
        for pod in before_pods:
            if not pod["metadata"]["name"].startswith("telemetry-datanode-"):
                continue
            node = pod["spec"]["nodeName"]
            if node == witness or node.endswith("control-plane"):
                continue
            colocated = [p for p in before_pods if p["spec"].get("nodeName") == node]
            if all(
                sum(
                    p["metadata"]["name"].startswith("telemetry-" + role + "-")
                    for p in colocated
                )
                <= 1
                for role in ("meta", "frontend", "datanode")
            ):
                candidates.append((node, colocated))
        if not candidates:
            raise RuntimeError(
                "no safe drain placement retaining witness and each role's quorum"
            )
        cordoned, affected = candidates[0]
        owned_node(cordoned)
        excluded = {p["metadata"]["name"] for p in affected}
        proof["drained_node"] = cordoned
        proof["affected_pods"] = identity(affected)
        kubectl(
            "drain",
            cordoned,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--timeout=120s",
        )
        wait_progress(case, load, after=now())
        budgets = json.loads(
            kubectl("get", "pdb", "telemetry-datanode", "-o", "json", capture=True)
        )
        if budgets["status"]["disruptionsAllowed"] != 0:
            raise RuntimeError(
                "expected no second datanode disruption while one local-PV pod is unavailable"
            )
        victim = next(
            p
            for p in pods()
            if p["metadata"]["name"].startswith("telemetry-datanode-") and ready(p)
        )
        eviction = {
            "apiVersion": "policy/v1",
            "kind": "Eviction",
            "metadata": {
                "name": victim["metadata"]["name"],
                "namespace": "greptime-lab",
            },
        }
        path = (
            "/api/v1/namespaces/greptime-lab/pods/"
            + victim["metadata"]["name"]
            + "/eviction"
        )
        rejected = subprocess.run(
            [*KUBE, "create", "--raw", path, "-f", "-"],
            input=json.dumps(eviction),
            text=True,
            capture_output=True,
            timeout=30,
        )
        proof["unsafe_eviction"] = {
            "returncode": rejected.returncode,
            "stdout": rejected.stdout,
            "stderr": rejected.stderr,
            "pdb": budgets,
        }
        if rejected.returncode == 0 or not any(
            word in rejected.stderr for word in ("TooManyRequests", "disruption budget")
        ):
            raise RuntimeError("second voluntary eviction was not rejected by PDB")
        current = json.loads(
            kubectl(
                "get", "pod", victim["metadata"]["name"], "-o", "json", capture=True
            )
        )
        if current["metadata"]["uid"] != victim["metadata"]["uid"]:
            raise RuntimeError("PDB rejection replaced the protected survivor")
        kubectl("uncordon", cordoned)
        cordoned = None
    elif mode == "partition":
        region = query(
            "SELECT region_id,peer_addr FROM information_schema.region_peers WHERE table_name='fleet_load' ORDER BY region_id"
        )[0]
        address = str(ipaddress.ip_address(region[1].split(":")[0]))
        target = next(p for p in before_pods if p["status"].get("podIP") == address)
        chain_node = target["spec"]["nodeName"]
        owned_node(chain_node)
        chain = "GTLAB_" + hashlib.sha256(case.encode()).hexdigest()[:10]
        run("docker", "exec", chain_node, "iptables", "-N", chain)
        run("docker", "exec", chain_node, "iptables", "-I", "FORWARD", "1", "-j", chain)
        run(
            "docker",
            "exec",
            chain_node,
            "iptables",
            "-A",
            chain,
            "-s",
            address,
            "-p",
            "tcp",
            "-m",
            "multiport",
            "--dports",
            "3002,4001",
            "-j",
            "DROP",
        )
        run(
            "docker",
            "exec",
            chain_node,
            "iptables",
            "-A",
            chain,
            "-d",
            address,
            "-p",
            "tcp",
            "--dport",
            "4001",
            "-j",
            "DROP",
        )
        proof["partitioned_region"] = region
        proof["partitioned_pod"] = target["metadata"]["name"]
        time.sleep(60)
        counters = run(
            "docker",
            "exec",
            chain_node,
            "iptables",
            "-L",
            chain,
            "-n",
            "-v",
            "-x",
            capture=True,
        )
        proof["drop_counters"] = counters
        if not any(
            line.split()[0].isdigit() and int(line.split()[0]) > 0 and "DROP" in line
            for line in counters.splitlines()
        ):
            raise RuntimeError(
                "partition installed but no matching traffic was dropped"
            )
        proof["regions_during_partition"] = query(
            "SELECT table_name,region_id,peer_id,status FROM information_schema.region_peers"
        )
        run("docker", "exec", chain_node, "iptables", "-D", "FORWARD", "-j", chain)
        run("docker", "exec", chain_node, "iptables", "-F", chain)
        run("docker", "exec", chain_node, "iptables", "-X", chain)
        chain = None
    elif mode in ("oom", "cpu"):
        excluded = {
            p["metadata"]["name"]
            for p in before_pods
            if p["metadata"]["name"].startswith("telemetry-datanode-")
        }
        resources = json.loads(json.dumps(original_resources))
        if mode == "oom":
            resources["requests"]["memory"] = "32Mi"
            resources["limits"]["memory"] = "64Mi"
        else:
            resources["requests"]["cpu"] = "100m"
            resources["limits"]["cpu"] = "100m"
        restore_resources = True
        patch({"datanode": {"template": {"main": {"resources": resources}}}})
        proof["injected_resources"] = resources
        if mode == "oom":
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                oom = [
                    {
                        "pod": p["metadata"]["name"],
                        "uid": p["metadata"]["uid"],
                        "status": status,
                    }
                    for p in pods()
                    if p["metadata"]["name"].startswith("telemetry-datanode-")
                    for status in p["status"].get("containerStatuses", [])
                    if status.get("lastState", {}).get("terminated", {}).get("reason")
                    == "OOMKilled"
                    or status.get("state", {}).get("terminated", {}).get("reason")
                    == "OOMKilled"
                ]
                if oom:
                    proof["oom_evidence"] = oom
                    break
                time.sleep(1)
            else:
                raise RuntimeError(
                    "bounded container memory limit did not produce proven OOMKilled"
                )
        else:
            wait_cluster()
            targets = [
                p
                for p in pods()
                if p["metadata"]["name"].startswith("telemetry-datanode-")
            ]
            first = {p["metadata"]["name"]: cpu_stat(p) for p in targets}
            # The 1k-row/s witness alone does not saturate 100m. Two bounded
            # high-cardinality analytical readers exercise the actual data path.
            fixture = json.loads((ROOT / "fixture.json").read_text())
            expected = fixture["series"] * fixture["points"]
            deadline = time.monotonic() + 30
            sql = "SELECT count(*) FROM (SELECT host,greptime_timestamp FROM fleet_load GROUP BY host,greptime_timestamp)"

            def analytical_reader():
                samples = []
                while time.monotonic() < deadline:
                    started = time.monotonic()
                    try:
                        result = json.loads(
                            kubectl(
                                "exec",
                                "client",
                                "--",
                                "client",
                                "sql",
                                sql,
                                capture=True,
                                timeout=20,
                            )
                        )
                    except (
                        subprocess.CalledProcessError,
                        subprocess.TimeoutExpired,
                    ) as error:
                        samples.append(
                            {
                                "outcome": "error",
                                "seconds": time.monotonic() - started,
                                "error": str(error),
                            }
                        )
                    else:
                        if result != [[expected]]:
                            raise RuntimeError(
                                "CPU-pressure analytics returned wrong baseline count"
                            )
                        samples.append(
                            {"outcome": "ok", "seconds": time.monotonic() - started}
                        )
                return samples

            with ThreadPoolExecutor(max_workers=2) as pool:
                readers = [pool.submit(analytical_reader) for _ in range(2)]
                proof["analytical_reads"] = [
                    sample for future in readers for sample in future.result()
                ]
            if not any(
                sample["outcome"] == "ok" for sample in proof["analytical_reads"]
            ):
                raise RuntimeError(
                    "CPU-pressure analytics never completed successfully"
                )
            second = {p["metadata"]["name"]: cpu_stat(p) for p in targets}
            proof["cpu_before"], proof["cpu_after"] = first, second
            if any(
                first[name]["container_id"] != second[name]["container_id"]
                for name in first
            ) or not any(
                second[name]["cpu_stat"]["nr_throttled"]
                > first[name]["cpu_stat"]["nr_throttled"]
                for name in first
            ):
                raise RuntimeError(
                    "actual CPU throttling was not proven on stable containers"
                )
        patch({"datanode": {"template": {"main": {"resources": original_resources}}}})
        if mode == "oom":
            # StatefulSet rollback can retain an unready pod from the bad revision.
            # Recreate only proven OOM victims after the restored template is visible.
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                stateful = json.loads(
                    kubectl(
                        "get",
                        "statefulset",
                        "telemetry-datanode",
                        "-o",
                        "json",
                        capture=True,
                    )
                )
                actual = stateful["spec"]["template"]["spec"]["containers"][0][
                    "resources"
                ]
                if actual == original_resources:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("restored resource template was not reconciled")
            recreated = []
            for victim in pods():
                if (
                    not victim["metadata"]["name"].startswith("telemetry-datanode-")
                    or victim["spec"]["containers"][0]["resources"]
                    == original_resources
                ):
                    continue
                if not any(
                    status.get("lastState", {}).get("terminated", {}).get("reason")
                    == "OOMKilled"
                    or status.get("state", {}).get("terminated", {}).get("reason")
                    == "OOMKilled"
                    for status in victim["status"].get("containerStatuses", [])
                ):
                    raise RuntimeError(
                        "unready resource revision lacks OOM evidence; refusing blind pod deletion"
                    )
                recreated.append(
                    {
                        "pod": victim["metadata"]["name"],
                        "old_uid": victim["metadata"]["uid"],
                    }
                )
                kubectl(
                    "delete",
                    "pod",
                    victim["metadata"]["name"],
                    "--wait=true",
                    "--timeout=120s",
                )
            proof["rollback_oom_pod_recreation"] = recreated
        restore_resources = False
    else:
        if cr["spec"]["version"] != "v1.3.0":
            raise RuntimeError(
                "upgrade requires a fresh fixture initialized on v1.3.0; never downgrade populated data"
            )
        excluded = {
            p["metadata"]["name"]
            for p in before_pods
            if p["metadata"]["name"].startswith(
                ("telemetry-datanode-", "telemetry-meta-", "telemetry-frontend-")
            )
        }
        run("docker", "pull", "greptime/greptimedb:v1.3.1", timeout=300)
        run(sys.executable, 'scripts/load_image.py', "greptime/greptimedb:v1.3.1", timeout=600)
        # SetDefaults derives spec.version from base.main.image in operator 1.3.0.
        patch(
            {
                "version": "v1.3.1",
                "base": {"main": {"image": "docker.io/greptime/greptimedb:v1.3.1"}},
            }
        )
        proof["upgrade_path"] = "v1.3.0 -> v1.3.1; pinned forward patch upgrade"
    wait_cluster()
    after_pods = pods()
    after = identity(after_pods)
    for name, value in before.items():
        if name not in excluded and after.get(name) != value:
            raise RuntimeError("unrelated survivor restarted: " + name)
    if mode == "upgrade":
        upgraded = [
            p
            for p in after_pods
            if p["metadata"]["name"].startswith(
                ("telemetry-datanode-", "telemetry-meta-", "telemetry-frontend-")
            )
        ]
        if len(upgraded) != 8 or any(
            not p["spec"]["containers"][0]["image"].endswith(":v1.3.1")
            for p in upgraded
        ):
            raise RuntimeError("all native roles did not reach the target version")
        proof["upgraded_roles"] = identity(upgraded)
    proof["unchanged_survivors"] = {
        name: value for name, value in before.items() if name not in excluded
    }
    wait_progress(case, load, after=now())
    reader.mark("after")
    reader.progress()
    (ROOT / ("stop-" + case)).touch()
    if load.wait(timeout=150) != 0:
        raise RuntimeError("producer failed")
    observations = reader.finish()
    run("make", "test", timeout=300)
    report = {
        "case": case,
        "transition_and_verification_seconds": time.monotonic() - begin,
        "proof": proof,
        "observations": observations,
        "reconciliation": json.loads((ROOT / "recovery-history.json").read_text()),
        "native_regions": query(
            "SELECT table_name,region_id,peer_id,status FROM information_schema.region_peers"
        ),
    }
    (ROOT / (case + "-result.json")).write_text(json.dumps(report, indent=2) + "\n")
except Exception as error:
    (ROOT / (case + "-failure.json")).write_text(
        json.dumps({"case": case, "error": str(error), "proof": proof}, indent=2) + "\n"
    )
    raise
finally:
    if cordoned:
        kubectl("uncordon", cordoned)
    if chain:
        for arguments in (("-D", "FORWARD", "-j", chain), ("-F", chain), ("-X", chain)):
            subprocess.run(
                ["docker", "exec", chain_node, "iptables", *arguments],
                check=False,
                timeout=20,
            )
    if restore_resources:
        patch({"datanode": {"template": {"main": {"resources": original_resources}}}})
    (ROOT / ("stop-" + case)).touch()
    try:
        load.wait(timeout=30)
    except subprocess.TimeoutExpired:
        load.terminate()
        load.wait(timeout=10)
    reader.close()
    log.close()
