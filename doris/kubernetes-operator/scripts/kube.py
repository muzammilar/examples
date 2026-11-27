"""Native CLI access limited to the owned Doris context and namespace."""

import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, KUBECONFIG=str(ROOT / "results/kubeconfig"))
KUBE = [
    str(ROOT / "results/tools/kubectl"),
    "--context",
    "kind-doris-kubernetes-operator",
    "-n",
    "doris-lab",
]


def run(*args, data=None, check=True, timeout=60):
    result = subprocess.run(
        [*KUBE, *args],
        input=data,
        text=True,
        capture_output=True,
        env=ENV,
        timeout=timeout,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr)
    return result


def get(resource, name=None):
    args = [
        "get",
        resource,
        *([name] if name else []),
        "--ignore-not-found",
        "-o",
        "json",
    ]
    text = run(*args).stdout
    return json.loads(text) if text.strip() else None


def query(sql):
    # Use a concrete example-owned FE pod, not the host's database endpoints.
    pods = get("pods")["items"]
    ready = [
        p["metadata"]["name"]
        for p in pods
        if p["metadata"].get("labels", {}).get("lab.example/role") == "frontend"
        and p.get("status", {}).get("phase") == "Running"
    ]
    if not ready:
        raise RuntimeError("No owned running FE")
    return run(
        "exec",
        sorted(ready)[0],
        "--",
        "mysql",
        "--batch",
        "--raw",
        "-h127.0.0.1",
        "-P9030",
        "-uroot",
        "-e",
        sql,
    )
