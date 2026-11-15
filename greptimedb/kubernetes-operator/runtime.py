"""Shared bounded waits and the external journal for operator experiments."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

KUBE = ["kubectl", "--context", "kind-greptime-operator", "--namespace", "greptime-lab"]
ROOT = Path("results")
os.environ["KUBECONFIG"] = str(ROOT.resolve() / "kubeconfig")


def kubectl(*args, capture=False):
    return subprocess.run(
        [*KUBE, *args], check=True, text=True, capture_output=capture, timeout=180
    ).stdout


def query(sql):
    return json.loads(
        kubectl("exec", "client", "--", "client", "sql", sql, capture=True)
    )


def records(case):
    path = ROOT / ("fault-" + case + ".jsonl")
    if not path.exists():
        return []
    # The encoder can be observed during its final write; only parse full lines.
    lines = path.read_text().split("\n")[:-1]
    return [json.loads(line) for line in lines]


def wait_progress(case, load, after=None, timeout=300):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if load.poll() is not None:
            raise RuntimeError("producer exited before recovery progress")
        acks = [
            r
            for r in records(case)
            if r["stage"] == "ack"
            and (
                after is None
                or datetime.fromisoformat(r["at"].replace("Z", "+00:00")) > after
            )
        ]
        if len(acks) >= 3:
            return acks
        time.sleep(1)
    raise RuntimeError("no three new acknowledged batches before recovery deadline")


def now():
    return datetime.now(timezone.utc)


def ready(pod):
    return not pod["metadata"].get("deletionTimestamp") and any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in pod["status"].get("conditions", [])
    )


def pods():
    return json.loads(kubectl("get", "pods", "-o", "json", capture=True))["items"]


def wait_cluster(timeout=600):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        cr = json.loads(
            kubectl("get", "greptimedbcluster", "telemetry", "-o", "json", capture=True)
        )
        status = cr.get("status", {})
        controllers = json.loads(
            kubectl("get", "deployments,statefulsets", "-o", "json", capture=True)
        )["items"]
        converged = all(
            c.get("status", {}).get("observedGeneration", 0)
            >= c["metadata"]["generation"]
            and c.get("status", {}).get("updatedReplicas", 0)
            == c["spec"].get("replicas", 1)
            and c.get("status", {}).get("readyReplicas", 0)
            == c["spec"].get("replicas", 1)
            and (
                c["kind"] != "StatefulSet"
                or c["status"].get("currentRevision")
                == c["status"].get("updateRevision")
            )
            for c in controllers
        )
        if (
            converged
            and status.get("observedGeneration") == cr["metadata"]["generation"]
            and all(
                status.get(role, {}).get("readyReplicas", 0)
                == cr["spec"][role]["replicas"]
                for role in ("meta", "datanode", "frontend")
            )
            and all(ready(p) for p in pods())
        ):
            native = query("SELECT status FROM information_schema.region_peers")
            if native and all(row[0] == "ALIVE" for row in native):
                return cr
        time.sleep(2)
    raise RuntimeError("latest operator generation and native regions did not converge")
