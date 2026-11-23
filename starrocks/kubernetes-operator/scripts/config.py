"""Generate the owned kind topology, pinned native configuration and operator CR."""

import json
import os
from pathlib import Path
import subprocess

root = Path.cwd()
results = root / "results"
results.mkdir(exist_ok=True)
os.environ["KUBECONFIG"] = str(results / "kubeconfig")
cluster, namespace = "starrocks-operator", "starrocks-lab"
workers = [cluster + "-worker" + (str(i + 1) if i else "") for i in range(3)]
items = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}]
nodes = [
    {
        "role": "control-plane",
        "extraMounts": [{"hostPath": str(results), "containerPath": "/lab-results"}],
    }
]
for i in range(3):
    nodes.append(
        {"role": "worker", "labels": {"topology.kubernetes.io/zone": "zone" + str(i)}}
    )
(root / "kind-config.json").write_text(
    json.dumps(
        {"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4", "nodes": nodes},
        indent=2,
    )
    + "\n"
)


def native_config(role):
    # Preserve upstream JVM/runtime flags and only change the documented lab caps
    # and persistent paths. No copied hand-maintained vendor config.
    image = f"starrocks/{role}-ubuntu:4.1.7"
    text = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "cat",
            image,
            f"/opt/starrocks/{role}/conf/{role}.conf",
        ],
        text=True,
    )
    text = text.replace("-Xmx8192m", "-Xmx640m") if role == "fe" else text
    text += (
        "\nmeta_dir = /opt/starrocks/fe/meta\nmaster_sync_policy = SYNC\nreplica_sync_policy = SYNC\nreplica_ack_policy = SIMPLE_MAJORITY\n"
        if role == "fe"
        else "\nstorage_root_path = /opt/starrocks/be/storage\nmem_limit = 1024M\nsync_tablet_meta = true\n"
    )
    return text


spec = {"waitForFullRollout": True}
for role in ("fe", "be"):
    items.append(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": role + "-config", "namespace": namespace},
            "data": {role + ".conf": native_config(role)},
        }
    )
    name = "starRocksFeSpec" if role == "fe" else "starRocksBeSpec"
    spec[name] = {
        "image": f"starrocks/{role}-ubuntu:4.1.7",
        "imagePullPolicy": "IfNotPresent",
        "replicas": 3,
        "requests": {"cpu": "100m", "memory": "256Mi"},
        "limits": {
            "cpu": "500m" if role == "fe" else "1",
            "memory": "1Gi" if role == "fe" else "1536Mi",
        },
        "configMapInfo": {
            "configMapName": role + "-config",
            "resolveKey": role + ".conf",
        },
        "storageVolumes": [
            {
                "name": role + ("-meta" if role == "fe" else "-data"),
                "storageSize": "2Gi",
                "mountPath": "/opt/starrocks/fe/meta"
                if role == "fe"
                else "/opt/starrocks/be/storage",
            }
        ],
        "terminationGracePeriodSeconds": 60,
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "kubernetes.io/hostname",
                                    "operator": "In",
                                    "values": workers,
                                }
                            ]
                        }
                    ]
                }
            },
            "podAntiAffinity": {
                "preferredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "weight": 100,
                        "podAffinityTerm": {
                            "labelSelector": {
                                "matchLabels": {
                                    "app.starrocks.ownerreference/name": "orders-"
                                    + role,
                                    "app.kubernetes.io/component": role,
                                }
                            },
                            "topologyKey": "kubernetes.io/hostname",
                        },
                    }
                ]
            },
        },
    }
    items.append(
        {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": {"name": role + "-quorum", "namespace": namespace},
            "spec": {
                "minAvailable": 2,
                "selector": {
                    "matchLabels": {
                        "app.starrocks.ownerreference/name": "orders-" + role,
                        "app.kubernetes.io/component": role,
                    }
                },
            },
        }
    )
items.append(
    {
        "apiVersion": "starrocks.com/v1",
        "kind": "StarRocksCluster",
        "metadata": {"name": "orders", "namespace": namespace},
        "spec": spec,
    }
)
(root / "cluster.json").write_text(
    json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2) + "\n"
)
frontends = ",".join(
    f"orders-fe-{i}.orders-fe-search.{namespace}.svc.cluster.local" for i in range(3)
)
backends = ",".join(
    f"orders-be-{i}.orders-be-search.{namespace}.svc.cluster.local" for i in range(3)
)
workload = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {"name": "orders-workload", "namespace": namespace},
    "spec": {
        "nodeSelector": {"kubernetes.io/hostname": cluster + "-control-plane"},
        "tolerations": [
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ],
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "orders-workload",
                "image": "starrocks-operator-workload:dev",
                "imagePullPolicy": "Never",
                "command": ["sleep", "infinity"],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "2", "memory": "2Gi"},
                },
                "env": [
                    {"name": key, "value": value}
                    for key, value in {
                        "SYSTEMS": "starrocks",
                        "COUNT": os.environ.get("COUNT", "1000000"),
                        "FAULT_BATCH_ROWS": os.environ.get("FAULT_BATCH_ROWS", "1000"),
                        "FAULT_PAUSE_MS": os.environ.get("FAULT_PAUSE_MS", "1000"),
                        "SR_FRONTENDS": frontends,
                        "SR_BACKENDS": backends,
                    }.items()
                ],
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
(root / "workload.json").write_text(json.dumps(workload, indent=2) + "\n")
