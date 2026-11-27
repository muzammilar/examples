"""Native kubectl access confined to this example's context and namespace."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, KUBECONFIG=str(ROOT / "results/kubeconfig"))
KUBE = [str(ROOT / "results/tools/kubectl"), "--context", "kind-spark-kubernetes-operator", "-n", "spark-lab"]


def run(*args, data=None, check=True, timeout=60):
    result = subprocess.run([*KUBE, *args], input=data, env=ENV, text=True,
                            capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"kubectl {' '.join(args)}: {result.stderr}")
    return result


def get(resource, name):
    text = run("get", resource, name, "--ignore-not-found", "-o", "json").stdout
    return json.loads(text) if text.strip() else None


def create(obj):
    return json.loads(run("create", "-f", "-", "-o", "json", data=json.dumps(obj)).stdout)


def pods():
    return json.loads(run("get", "pods", "-o", "json").stdout)["items"]


def sample():
    observed = pods()
    counters = {}
    for pod in observed:
        if pod.get("status", {}).get("phase") != "Running":
            continue
        name = pod["metadata"]["name"]
        result = run("exec", name, "--", "sh", "-c",
                     "for f in cpu.stat memory.current memory.peak io.stat; do echo $f; cat /sys/fs/cgroup/$f; done",
                     check=False, timeout=15)
        counters[name] = {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    return {"pods": observed, "cgroups": counters}


def job_logs(name):
    return run("logs", "job/" + name, "--all-containers=true", check=False).stdout
