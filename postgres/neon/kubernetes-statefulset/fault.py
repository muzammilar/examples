"""Owned StatefulSet/worker failures with independent readers, writers and WAL oracle."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from production_observations import summarize, timestamp

ROOT = Path("results")
os.environ["KUBECONFIG"] = str(ROOT.resolve() / "kubeconfig")
KUBE = ["kubectl", "--context", "kind-neon-storage", "-n", "neon-lab"]
mode = sys.argv[1]
if mode not in ("wal-process", "wal-disk", "compute-process", "compute-cache", "node"):
    raise ValueError(mode)
target = "compute" if mode.startswith("compute") else "sk0"
if mode == "node":
    target = "sk1"
case = mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def now():
    return datetime.now(timezone.utc)


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


def tool(action, capture=False):
    return kube(
        "exec", "client", "--", "sh", "/app/entrypoint.sh", action, capture=capture
    )


def pod(name):
    return json.loads(kube("get", "pod", name, "-o", "json", capture=True))


def events(prefix):
    path = ROOT / (prefix + case + ".jsonl")
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(after):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("writer/reader exited during recovery")
        acks = sum(
            e["stage"] == "ack" and timestamp(e["at"]) > after for e in events("fault-")
        )
        reads = sum(
            e["outcome"] == "ok" and timestamp(e["at"]) > after
            for e in events("reads-")
        )
        if acks >= 3 and reads >= 3:
            return
        time.sleep(1)
    raise RuntimeError("no fresh writer and reader progress")


def lsn(value):
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def wal_barrier():
    frozen_lsn = max(
        (e["flush_lsn"] for e in events("fault-") if e["stage"] == "ack"), key=lsn
    )
    expected = lsn(frozen_lsn)
    deadline = time.monotonic() + 180
    last_error = None
    while time.monotonic() < deadline:
        try:
            statuses = json.loads(tool("wal-status", capture=True))
        except RuntimeError as error:
            # A restarted Pod/Service may not serve the timeline yet. This is
            # incomplete native recovery, not a reason to stop the live load.
            last_error = str(error)
            time.sleep(2)
            continue
        if len(statuses) == 3 and all(
            lsn(s["flush_lsn"]) >= expected and lsn(s["commit_lsn"]) >= expected
            for s in statuses.values()
        ):
            break
        time.sleep(2)
    else:
        raise RuntimeError(
            "three native WAL copies did not converge: " + str(last_error)
        )
    return frozen_lsn, statuses


markers = [("before", now())]
logs, processes = [], []
for action in ("churn", "watch"):
    log = (ROOT / (case + "-" + action + ".log")).open("w")
    logs.append(log)
    processes.append(
        subprocess.Popen(
            [
                *KUBE,
                "exec",
                "client",
                "--",
                "env",
                "FAULT_CASE=" + case,
                "/usr/local/bin/workload",
                action,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    )
scaled_down, stopped_node = False, None
fault_evidence = {}
try:
    progress(markers[0][1])
    before_lsn, before_wal = wal_barrier()
    original = pod(target + "-0")
    uid = original["metadata"]["uid"]
    node = original["spec"]["nodeName"]
    cid = original["status"]["containerStatuses"][0]["containerID"].removeprefix(
        "containerd://"
    )
    owner = json.loads(run("docker", "inspect", node, capture=True))[0]
    if owner["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "neon-storage":
        raise RuntimeError("refusing foreign node")
    started = time.monotonic()
    markers.append(("outage", now()))
    if mode == "node":
        # sk1 is on the first worker; never stop the control plane/witness.
        node = "neon-storage-worker"
        owner = json.loads(run("docker", "inspect", node, capture=True))[0]
        if owner["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "neon-storage":
            raise RuntimeError("refusing foreign worker")
        run("docker", "kill", "--signal", "SIGKILL", node)
        stopped_node = node
        end = time.monotonic() + 120
        while time.monotonic() < end:
            value = json.loads(
                run(
                    "kubectl",
                    "--context",
                    "kind-neon-storage",
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
                fault_evidence["unavailable_node_condition"] = condition
                break
            time.sleep(1)
        else:
            raise RuntimeError("owned worker did not become unavailable")
        time.sleep(30)
        progress(markers[-1][1])
        markers.append(("recovery", now()))
        run("docker", "start", node)
        stopped_node = None
        run(
            "kubectl",
            "--context",
            "kind-neon-storage",
            "wait",
            "--for=condition=Ready",
            "node/" + node,
            "--timeout=180s",
        )
    elif mode in ("wal-disk", "compute-cache"):
        # Scale down and wait for deletion before deleting a claim: no old writer
        # remains attached to the lost disk. This is disk replacement, not SIGKILL.
        kube("scale", "statefulset/" + target, "--replicas=0")
        scaled_down = True
        kube("wait", "--for=delete", "pod/" + target + "-0", "--timeout=120s")
        claim = "data-" + target + "-0"
        volume = json.loads(kube("get", "pvc", claim, "-o", "json", capture=True))
        old_claim_uid = volume["metadata"]["uid"]
        kube("delete", "pvc", claim, "--wait=true", "--timeout=120s")
        time.sleep(30)
        if target == "sk0":
            progress(markers[-1][1])
        markers.append(("recovery", now()))
        kube("scale", "statefulset/" + target, "--replicas=1")
        scaled_down = False
        kube("rollout", "status", "statefulset/" + target, "--timeout=300s")
        replacement = json.loads(kube("get", "pvc", claim, "-o", "json", capture=True))
        if replacement["metadata"]["uid"] == old_claim_uid:
            raise RuntimeError("claim was not replaced")
        fault_evidence["replaced_pvc"] = {
            "name": claim,
            "old_uid": old_claim_uid,
            "new_uid": replacement["metadata"]["uid"],
        }
        if target == "sk0":
            # The pinned API is idempotent for an existing timeline. Retry while
            # the new Pod/Service starts; never delete an automatically recovered timeline.
            end = time.monotonic() + 180
            while time.monotonic() < end:
                try:
                    pulled = tool("recover-wal", capture=True)
                    (ROOT / (case + "-wal-pull.log")).write_text(pulled)
                    break
                except RuntimeError:
                    time.sleep(2)
            else:
                raise RuntimeError(
                    "replacement WAL recovery API did not become available"
                )
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
        # The old runtime container must leave RUNNING; a Ready pod alone is not proof.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", node, "crictl", "inspect", cid],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if (
                result.returncode != 0
                or json.loads(result.stdout)["status"]["state"] != "CONTAINER_RUNNING"
            ):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("old process remained running")
        markers.append(("recovery", now()))
    kube("rollout", "status", "statefulset/" + target, "--timeout=300s")
    progress(markers[-1][1])
    frozen_lsn, statuses = wal_barrier()
    markers.append(("after", now()))
    progress(markers[-1][1])
    (ROOT / ("stop-" + case)).touch()
    if any(process.wait(timeout=60) != 0 for process in processes):
        raise RuntimeError("workload failed")
    observations = summarize(
        ROOT / ("fault-" + case + ".jsonl"),
        ROOT / ("reads-" + case + ".jsonl"),
        markers,
        now(),
    )
    (ROOT / (case + "-observations.json")).write_text(
        json.dumps(observations, indent=2) + "\n"
    )
    if observations["contract_failures"]:
        raise RuntimeError(str(observations["contract_failures"]))
    tool("verify")
    result = {
        "case": case,
        "fault_evidence": fault_evidence,
        "target": target,
        "old_pod_uid": uid,
        "old_container_id": cid,
        "current_pod": pod(target + "-0")["status"]["containerStatuses"],
        "recovery_and_verification_seconds": time.monotonic() - started,
        "wal_status": statuses,
        "before_wal_status": before_wal,
        "before_frozen_ack_lsn": before_lsn,
        "frozen_ack_lsn": frozen_lsn,
        "observations": observations,
        "reconciliation": json.loads((ROOT / "recovery-history.json").read_text()),
    }
    (ROOT / (case + "-result.json")).write_text(json.dumps(result, indent=2) + "\n")
except Exception as error:
    (ROOT / (case + "-failure.json")).write_text(
        json.dumps({"case": case, "error": str(error)}, indent=2) + "\n"
    )
    raise
finally:
    if stopped_node:
        run("docker", "start", stopped_node)
    if scaled_down:
        kube("scale", "statefulset/" + target, "--replicas=1")
    (ROOT / ("stop-" + case)).touch()
    for process in processes:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
    for log in logs:
        log.close()
    with (ROOT / (case + "-pods.json")).open("w") as output:
        subprocess.run(
            [*KUBE, "get", "pods", "-o", "json"], stdout=output, check=False, timeout=30
        )
