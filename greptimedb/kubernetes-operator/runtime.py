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


def kubectl(*args, capture=False, timeout=180):
    return subprocess.run(
        [*KUBE, *args], check=True, text=True, capture_output=capture, timeout=timeout
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


def desired_pods_match(cr, current):
    # Operator 1.3.0 advances CR observedGeneration only on phase transitions.
    # Verify the actual supported desired fields instead of trusting that field.
    for role in ("meta", "datanode", "frontend"):
        desired = cr["spec"][role]
        members = [
            pod
            for pod in current
            if pod["metadata"]["name"].startswith("telemetry-" + role + "-")
        ]
        if len(members) != desired["replicas"]:
            return False
        template = desired.get("template", {})
        resources = template.get("main", {}).get("resources")
        for member in members:
            if (
                resources
                and member["spec"]["containers"][0].get("resources") != resources
            ):
                return False
            if any(
                member["metadata"].get("annotations", {}).get(key) != value
                for key, value in template.get("annotations", {}).items()
            ):
                return False
    return True


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
        current = pods()
        if (
            converged
            and desired_pods_match(cr, current)
            and all(
                status.get(role, {}).get("readyReplicas", 0)
                == cr["spec"][role]["replicas"]
                for role in ("meta", "datanode", "frontend")
            )
            and all(ready(p) for p in current)
        ):
            native = query("SELECT status FROM information_schema.region_peers")
            if native and all(row[0] == "ALIVE" for row in native):
                wait_baseline()
                return cr
        time.sleep(2)
    raise RuntimeError("desired operator resources and native regions did not converge")


def wait_baseline(timeout=120):
    """Keep the live workload running until every frontend sees the full fixture.

    Native ALIVE metadata does not establish completed replay or correct reads.
    Full value/body reconciliation remains mandatory after the workload stops.
    """
    fixture_path = ROOT / "fixture.json"
    if not fixture_path.exists():
        return
    fixture = json.loads(fixture_path.read_text())
    expected = {
        "fleet_load": fixture["series"] * fixture["points"],
        "events": fixture["logs"],
    }
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        observed = {}
        frontends = [
            p
            for p in pods()
            if p["metadata"]["name"].startswith("telemetry-frontend-") and ready(p)
        ]
        for frontend in frontends:
            name = frontend["metadata"]["name"]
            observed[name] = {}
            for table in expected:
                try:
                    observed[name][table] = json.loads(
                        kubectl(
                            "exec",
                            "client",
                            "--",
                            "env",
                            "GREPTIME=http://" + frontend["status"]["podIP"] + ":4000",
                            "client",
                            "sql",
                            "SELECT count(*) FROM " + table,
                            capture=True,
                            timeout=max(1, min(15, end - time.monotonic())),
                        )
                    )[0][0]
                except (
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                    json.JSONDecodeError,
                ) as error:
                    observed[name][table] = str(error)
        (ROOT / "baseline-visibility.json").write_text(
            json.dumps(
                {"at": now().isoformat(), "expected": expected, "frontends": observed},
                indent=2,
            )
            + "\n"
        )
        if len(frontends) == 2 and all(
            counts == expected for counts in observed.values()
        ):
            return
        time.sleep(2)
    raise RuntimeError(
        "baseline incomplete across frontends; see baseline-visibility.json; no data reset performed"
    )
