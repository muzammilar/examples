"""Bridge owned external WAL/S3/metadata endpoints into the operator namespace."""

import json
import os
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
namespace = "greptime-lab"
project = "greptime-kind-support"
ctx = "kind-greptime-operator"
results = root / "results"
results.mkdir(exist_ok=True)
os.environ["KUBECONFIG"] = str(results / "kubeconfig")
config = {
    "kind": "Cluster",
    "apiVersion": "kind.x-k8s.io/v1alpha4",
    "nodes": [
        {
            "role": "control-plane",
            "extraMounts": [
                {"hostPath": str(results), "containerPath": "/lab-results"}
            ],
        },
        *(
            {
                "role": "worker",
                "extraMounts": [
                    {"hostPath": str(results), "containerPath": "/lab-results"}
                ],
                "labels": {"topology.kubernetes.io/zone": f"zone{i}"},
            }
            for i in range(3)
        ),
    ],
}
(root / "kind-config.json").write_text(json.dumps(config, indent=2) + "\n")
if len(sys.argv) == 1:
    raise SystemExit(0)
resources = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}]
services = {
    **{f"meta-store{i}": [2379] for i in range(3)},
    **{f"wal{i}": [9092] for i in range(3)},
    "s3": [9000],
    "vminsert": [8480],
    "vmselect": [8481],
    "vlogs": [9428],
}
for name, ports in services.items():
    ids = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + project,
            "--filter",
            "label=com.docker.compose.service=" + name,
        ],
        text=True,
    ).splitlines()
    if len(ids) != 1:
        raise RuntimeError("expected one owned endpoint: " + name)
    info = json.loads(
        subprocess.check_output(["docker", "inspect", ids[0]], text=True)
    )[0]
    ip = info["NetworkSettings"]["Networks"][project + "_default"]["IPAddress"]
    metadata = {"name": name, "namespace": namespace}
    resources.append(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {
                "ports": [{"name": f"p{p}", "port": p, "targetPort": p} for p in ports]
            },
        }
    )
    resources.append(
        {
            "apiVersion": "discovery.k8s.io/v1",
            "kind": "EndpointSlice",
            "metadata": {
                "name": name + "-external",
                "namespace": namespace,
                "labels": {"kubernetes.io/service-name": name},
            },
            "addressType": "IPv4",
            "endpoints": [{"addresses": [ip], "conditions": {"ready": True}}],
            "ports": [{"name": f"p{p}", "port": p, "protocol": "TCP"} for p in ports],
        }
    )
subprocess.run(
    ["kubectl", "--context", ctx, "apply", "-f", "-"],
    input=json.dumps({"apiVersion": "v1", "kind": "List", "items": resources}),
    text=True,
    check=True,
)
client = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": "client", "namespace": namespace},
    "spec": {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "client",
                "image": project + "-client:dev",
                "imagePullPolicy": "Never",
                "command": ["sleep", "infinity"],
                "env": [
                    {"name": k, "value": v}
                    for k, v in {
                        "GREPTIME": "http://telemetry-frontend:4000",
                        "VM_WRITE": "http://vminsert:8480/insert/0/prometheus/api/v1/write",
                        "VM_QUERY": "http://vmselect:8481/select/0/prometheus",
                        "VLOGS": "http://vlogs:9428",
                        "SERIES": os.environ.get("SERIES", "10000"),
                        "POINTS": os.environ.get("POINTS", "100"),
                        "LOGS": os.environ.get("LOGS", "1000000"),
                    }.items()
                ],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "256Mi"},
                    "limits": {"cpu": "2", "memory": "2Gi"},
                },
                "volumeMounts": [{"name": "results", "mountPath": "/results"}],
            }
        ],
        "volumes": [
            {
                "name": "results",
                "hostPath": {"path": "/lab-results", "type": "Directory"},
            }
        ],
    },
}
(root / "client.json").write_text(json.dumps(client, indent=2) + "\n")
