"""Native Neon StatefulSets; owned external S3/PostgreSQL comparison dependencies."""

import json
import os
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
results = root / "results"
results.mkdir(exist_ok=True)
os.environ["KUBECONFIG"] = str(results / "kubeconfig")
cluster = "neon-storage"
context = "kind-" + cluster
namespace = "neon-lab"
project = "neon-kind-support"
base = root / "dependencies"
nodes = [cluster + "-control-plane", cluster + "-worker", cluster + "-worker2"]
kind_nodes = [
    {
        "role": "control-plane",
        "kubeadmConfigPatches": [
            "kind: InitConfiguration\nnodeRegistration:\n  taints: []\n"
        ],
    },
    {"role": "worker"},
    {"role": "worker"},
]
for i, node in enumerate(kind_nodes):
    node["labels"] = {"topology.kubernetes.io/zone": f"zone{i}"}
    node["extraMounts"] = [{"hostPath": str(results), "containerPath": "/lab-results"}]
(root / "kind-config.json").write_text(
    json.dumps(
        {
            "kind": "Cluster",
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "nodes": kind_nodes,
        },
        indent=2,
    )
    + "\n"
)
env = {**os.environ, "COMPOSE_PROJECT_NAME": project}
compose = json.loads(
    subprocess.check_output(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "--profile",
            "workload",
            "config",
            "--format",
            "json",
        ],
        cwd=base,
        env=env,
        text=True,
    )
)
images = {
    key: compose["services"][key]["image"]
    for key in ("broker", "pageserver", "compute", "sk0", "runner")
}
images["source-storage"] = images["broker"]
images["source-compute"] = images["compute"]
# Docker's digest-only export creates transient import aliases in containerd.
# Stable local tags retain the original pinned digest as their source.
images["broker"] = images["pageserver"] = (
    "neon-kind-storage:sha-" + images["source-storage"].split("sha256:")[1][:12]
)
images["compute"] = (
    "neon-kind-compute:sha-" + images["source-compute"].split("sha256:")[1][:12]
)
images["sk0"] = "neon-kind-safekeeper:dev"
(root / "images.json").write_text(json.dumps(images, indent=2) + "\n")
if len(sys.argv) == 1:
    raise SystemExit(0)
objects = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}]


def service(name, ports):
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "selector": {"app": name},
            "ports": [{"name": f"p{p}", "port": p, "targetPort": p} for p in ports],
        },
    }


for name, port in [("s3", 9000), ("pg0", 5432), ("pg1", 5432), ("pg2", 5432)]:
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
    ).split()
    if len(ids) != 1:
        raise RuntimeError("expected one owned external endpoint: " + name)
    info = json.loads(
        subprocess.check_output(["docker", "inspect", ids[0]], text=True)
    )[0]
    address = info["NetworkSettings"]["Networks"][project + "_default"]["IPAddress"]
    endpoint_service = service(name, [port])
    del endpoint_service["spec"]["selector"]
    objects.append(endpoint_service)
    objects.append(
        {
            "apiVersion": "discovery.k8s.io/v1",
            "kind": "EndpointSlice",
            "metadata": {
                "name": name + "-external",
                "namespace": namespace,
                "labels": {"kubernetes.io/service-name": name},
            },
            "addressType": "IPv4",
            "endpoints": [{"addresses": [address], "conditions": {"ready": True}}],
            "ports": [{"name": f"p{port}", "port": port, "protocol": "TCP"}],
        }
    )

page_config = (
    (base / "config" / "pageserver.toml")
    .read_text()
    .replace(
        'prefix_in_bucket="neon/pageserver"', 'prefix_in_bucket="neon-kind/pageserver"'
    )
)
template = json.loads((base / "config" / "compute-template.json").read_text())
# All control/application connections use TCP. An emptyDir survives a container
# restart within the same Pod, so do not depend on it clearing Unix socket locks.
template["spec"]["cluster"]["settings"].append(
    {"name": "unix_socket_directories", "value": "", "vartype": "string"}
)
objects.append(
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "neon-config", "namespace": namespace},
        "data": {
            "pageserver.toml": page_config,
            "identity.toml": (base / "config" / "identity.toml").read_text(),
            "compute-template.json": json.dumps(template),
        },
    }
)


def stateful(name, image, command, args, ports, cpu, memory, node, data=None):
    container = {
        "name": name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": command,
        "args": args,
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": cpu, "memory": memory},
        },
        "ports": [{"containerPort": port} for port in ports],
    }
    if ports:
        container["readinessProbe"] = {
            "tcpSocket": {"port": ports[0]},
            "periodSeconds": 3,
            "failureThreshold": 60,
        }
    pod = {
        "nodeSelector": {"kubernetes.io/hostname": node},
        "terminationGracePeriodSeconds": 30,
        "containers": [container],
    }
    spec = {
        "serviceName": name,
        "replicas": 1,
        "selector": {"matchLabels": {"app": name}},
        "template": {"metadata": {"labels": {"app": name}}, "spec": pod},
    }
    if data:
        container["volumeMounts"] = [{"name": "data", "mountPath": data}]
        pod["securityContext"] = {"fsGroup": 1000}
        spec["volumeClaimTemplates"] = [
            {
                "metadata": {"name": "data"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "2Gi"}},
                },
            }
        ]
    objects.extend(
        [
            service(name, ports),
            {
                "apiVersion": "apps/v1",
                "kind": "StatefulSet",
                "metadata": {"name": name, "namespace": namespace},
                "spec": spec,
            },
        ]
    )
    return container, pod


stateful(
    "broker",
    images["broker"],
    ["storage_broker"],
    ["--listen-addr=0.0.0.0:50051"],
    [50051],
    "500m",
    "256Mi",
    nodes[0],
)
page, pod = stateful(
    "pageserver",
    images["pageserver"],
    ["/bin/sh", "-ec"],
    [
        "mkdir -p /data/.neon; cp /config/*.toml /data/.neon/; exec pageserver -D /data/.neon"
    ],
    [9898, 6400],
    "2",
    "2Gi",
    nodes[0],
    "/data",
)
page["env"] = [
    {"name": name, "value": value}
    for name, value in compose["services"]["pageserver"]["environment"].items()
]
page["volumeMounts"].append(
    {"name": "config", "mountPath": "/config", "readOnly": True}
)
pod["volumes"] = [{"name": "config", "configMap": {"name": "neon-config"}}]
for i in range(3):
    name = f"sk{i}"
    args = [
        "--listen-pg=0.0.0.0:5454",
        f"--advertise-pg={name}:5454",
        "--listen-http=0.0.0.0:7676",
        f"--id={i + 1}",
        "--peer-recovery=true",
        f"--availability-zone=zone{i}",
        "--broker-endpoint=http://broker:50051",
        "-D",
        "/data",
        "--remote-storage={endpoint='http://s3:9000',bucket_name='lab-objects',bucket_region='us-east-1',prefix_in_bucket='neon-kind/safekeeper/'}",
    ]
    container, _ = stateful(
        name,
        images["sk0"],
        ["safekeeper"],
        args,
        [7676, 5454],
        "1",
        "512Mi",
        nodes[i],
        "/data",
    )
    container["env"] = page["env"]
client = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": "client", "namespace": namespace},
    "spec": {
        "nodeSelector": {"kubernetes.io/hostname": nodes[0]},
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "client",
                "image": images["runner"],
                "imagePullPolicy": "IfNotPresent",
                "command": ["sleep", "infinity"],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "2", "memory": "2Gi"},
                },
                "env": [
                    {"name": "ROWS", "value": os.environ.get("ROWS", "1000000")},
                    {
                        "name": "NEON_DSN",
                        "value": "postgresql://cloud_admin:cloud_admin@compute:55433/postgres",
                    },
                    {
                        "name": "BRANCH_DSN",
                        "value": "postgresql://cloud_admin:cloud_admin@branch:55433/postgres",
                    },
                    {
                        "name": "PG_DSN",
                        "value": "postgresql://postgres:local-only-postgres@pg0:5432/postgres",
                    },
                ],
                "volumeMounts": [
                    {"name": "results", "mountPath": "/results"},
                    {"name": "config", "mountPath": "/config", "readOnly": True},
                ],
            }
        ],
        "volumes": [
            {
                "name": "results",
                "hostPath": {"path": "/lab-results", "type": "Directory"},
            },
            {"name": "config", "configMap": {"name": "neon-config"}},
        ],
    },
}
objects.append(client)
(root / "storage.json").write_text(
    json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}, indent=2) + "\n"
)
for name in ("compute", "branch"):
    start = len(objects)
    args = compose["services"][name]["command"]
    # The native image's command specifies its compute-id and config file.
    args = [
        "/config/" + name + ".json" if arg == "/config/config.json" else arg
        for arg in args
    ]
    container, pod = stateful(
        name,
        images["compute"],
        ["compute_ctl"],
        args,
        [55433],
        "2",
        "2Gi",
        nodes[0] if name == "compute" else nodes[2],
        "/var/db/postgres",
    )
    container["volumeMounts"].extend(
        [
            {"name": "config", "mountPath": "/config", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ]
    )
    pod["volumes"] = [
        {
            "name": "config",
            "hostPath": {"path": "/lab-results/generated", "type": "Directory"},
        },
        {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}},
    ]
    (root / (name + ".json")).write_text(
        json.dumps(
            {"apiVersion": "v1", "kind": "List", "items": objects[start:]}, indent=2
        )
        + "\n"
    )
