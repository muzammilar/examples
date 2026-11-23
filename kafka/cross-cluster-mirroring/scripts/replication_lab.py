"""Owned kind lifecycle and local workload Jobs for the mirroring recovery tests."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "results"
DATA = STATE / "production"
CLUSTER = "kafka-cross-cluster-mirroring"
CONTEXT = "kind-" + CLUSTER
CONFIG = STATE / "kubeconfig"
KUBE = ["kubectl", "--kubeconfig", str(CONFIG), "--context", CONTEXT, "-n", "kafka"]
ENV = os.environ | {"KUBECONFIG": str(CONFIG)}
PRODUCER = "kafka-mirror-witness-producer:dev"
OBSERVER = "kafka-mirror-replication-observer:dev"


def call(args, timeout=90, stdin=None):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=ENV,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{args}: exit={result.returncode}: {result.stderr[-5000:]} {result.stdout[-1000:]}"
        )
    return result.stdout


def save(path, value, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise FileExistsError(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def kube(*args):
    return json.loads(call(KUBE + list(args)))


def wait(check, description, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(description + " timed out")


def node(name):
    rows = json.loads(call(["docker", "inspect", name], timeout=30))
    if len(rows) != 1:
        raise RuntimeError("ambiguous kind node")
    record = rows[0]
    if (
        record["Name"].lstrip("/") != name
        or record["Config"]["Labels"].get("io.x-k8s.kind.cluster") != CLUSTER
        or not record["State"]["Running"]
    ):
        raise RuntimeError("kind node ownership/state mismatch")
    return record


def owner():
    receipt = json.loads((STATE / "replication-owner.json").read_text())
    nodes = {name: node(name)["Id"] for name in receipt["nodes"]}
    if nodes != receipt["nodes"]:
        raise RuntimeError("owned kind node identities changed")
    control = node(CLUSTER + "-control-plane")
    mounts = [m for m in control["Mounts"] if m["Destination"] == "/var/lab/mirroring"]
    sources = {str(DATA.resolve())}
    if sys.platform == "darwin":
        # Docker Desktop exposes macOS host paths through its Linux VM.
        sources.add("/host_mnt" + str(DATA.resolve()))
    if (
        len(mounts) != 1
        or mounts[0]["Type"] != "bind"
        or mounts[0]["Source"] not in sources
        or not mounts[0]["RW"]
    ):
        raise RuntimeError("external host witness mount is unavailable")
    return receipt


def kind_up():
    if STATE.is_symlink() or DATA.is_symlink() or CONFIG.is_symlink():
        raise RuntimeError("refusing symlinked lab state")
    STATE.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    clusters = call(["kind", "get", "clusters"]).splitlines()
    if CLUSTER in clusters:
        owner()
        return
    if (STATE / "replication-owner.json").exists():
        raise RuntimeError(
            "old owner receipt exists; finish kind-down before recreating"
        )
    config = json.loads((ROOT / "config/kind-config.json").read_text())
    if (
        config["name"] != CLUSTER
        or config["nodes"][0]["extraMounts"][0]["hostPath"] != "@WITNESS_HOST_PATH@"
    ):
        raise RuntimeError("invalid example-owned kind template")
    config["nodes"][0]["extraMounts"][0]["hostPath"] = str(DATA.resolve())
    save(STATE / "kind-replication.json", config, replace=True)
    call(
        ["kind", "create", "cluster", "--config", str(STATE / "kind-replication.json")],
        timeout=360,
    )
    names = [
        CLUSTER + "-control-plane",
        CLUSTER + "-worker",
        CLUSTER + "-worker2",
        CLUSTER + "-worker3",
    ]
    save(
        STATE / "replication-owner.json",
        {
            "owner": uuid.uuid4().hex,
            "nodes": {name: node(name)["Id"] for name in names},
        },
    )
    owner()


def job(case, side, role, output=None):
    receipt = owner()
    root = DATA / "cases" / case / side
    plan = json.loads((root / "case.json").read_text())
    if (
        plan["run_id"] != case
        or side not in {"east", "west"}
        or role not in {"producer", "reader", "verify"}
    ):
        raise RuntimeError("invalid workload identity")
    if os.getuid() == 0:
        raise RuntimeError("use a non-root host UID for external witnesses")
    name = f"{role}-{side}-{case[:12]}-" + uuid.uuid4().hex[:8]
    path = "/var/lab/mirroring/cases/" + case + "/" + side
    verifier = role == "verify"
    image = PRODUCER if role == "producer" else OBSERVER
    binary = (
        "/usr/local/bin/order-producer"
        if role == "producer"
        else "/usr/local/bin/order-consumer"
    )
    volumes = [{"name": "witness", "hostPath": {"path": path, "type": "Directory"}}]
    mounts = [{"name": "witness", "mountPath": "/witness", "readOnly": verifier}]
    environment = [{"name": "WITNESS_DIR", "value": "/witness"}]
    if verifier:
        if output is None or not output.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("verification output must belong to this actor")
        output.mkdir(parents=True)
        volumes.append(
            {
                "name": "output",
                "hostPath": {
                    "path": "/var/lab/mirroring/" + str(output.relative_to(DATA)),
                    "type": "Directory",
                },
            }
        )
        mounts.append({"name": "output", "mountPath": "/output"})
        environment += [
            {"name": "MODE", "value": "verify"},
            {"name": "OUTPUT_DIR", "value": "/output"},
        ]
        gate = "/output/start"
    else:
        gate = "/witness/start-" + role
    labels = {
        "lab.example/owner": receipt["owner"],
        "lab.example/case": case,
        "lab.example/role": role,
    }
    specification = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": plan["duration_seconds"] + 300,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "nodeName": CLUSTER + "-control-plane",
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "tolerations": [
                        {
                            "key": "node-role.kubernetes.io/control-plane",
                            "operator": "Exists",
                            "effect": "NoSchedule",
                        }
                    ],
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": os.getuid(),
                        "runAsGroup": os.getgid(),
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": volumes,
                    "containers": [
                        {
                            "name": role,
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "sh",
                                "-ec",
                                'for i in $(seq 1 300); do if [ -f "$1" ]; then exec "$2"; fi; sleep 1; done; exit 1',
                                "gate",
                                gate,
                                binary,
                            ],
                            "env": environment,
                            "volumeMounts": mounts,
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {
                                    "cpu": "2" if verifier else "1",
                                    "memory": "2Gi"
                                    if verifier
                                    else ("512Mi" if role == "producer" else "1Gi"),
                                },
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    }
    call(KUBE + ["create", "-f", "-"], stdin=json.dumps(specification))
    pods = wait(
        lambda: kube("get", "pods", "-l", "job-name=" + name, "-o", "json")["items"],
        "workload Pod creation",
        60,
    )
    if len(pods) != 1:
        raise RuntimeError("ambiguous workload Pod")
    pod = pods[0]["metadata"]["name"]
    call(
        KUBE + ["wait", "pod/" + pod, "--for=condition=Ready", "--timeout=180s"],
        timeout=200,
    )
    expected = call(
        ["docker", "run", "--rm", "--entrypoint", "sha256sum", image, binary]
    ).split()[0]
    actual = call(KUBE + ["exec", pod, "-c", role, "--", "sha256sum", binary]).split()[
        0
    ]
    if expected != actual:
        raise RuntimeError("stale workload executable; start gate remains closed")
    save(
        (output if verifier else root)
        / ("verify-job.json" if verifier else role + "-job.json"),
        {"name": name, "pod": pod, "binary_sha256": actual, "manifest": specification},
    )
    (output / "start" if verifier else root / ("start-" + role)).touch(exist_ok=False)
    return name


def completed(name, evidence):
    observed = kube("get", "job", name, "-o", "json")
    if observed.get("status", {}).get("failed"):
        save(evidence / ("failed-job-" + name + ".json"), observed, replace=True)
        (evidence / ("failed-job-" + name + ".log")).write_text(
            call(KUBE + ["logs", "job/" + name])
        )
        raise RuntimeError("workload failed: " + name)
    if observed.get("status", {}).get("succeeded") != 1:
        return False
    save(evidence / ("completed-job-" + name + ".json"), observed)
    (evidence / ("completed-job-" + name + ".log")).write_text(
        call(KUBE + ["logs", "job/" + name])
    )
    return True


def record_images(*tags):
    allowed = {
        PRODUCER,
        OBSERVER,
        "kafka-mirror-producer:dev",
        "kafka-mirror-consumer:dev",
    }
    if not tags or not set(tags) <= allowed:
        raise ValueError("only this example's built images can be recorded")
    path = STATE / "image-ownership.json"
    images = json.loads(path.read_text()) if path.exists() else {}
    for tag in tags:
        rows = json.loads(call(["docker", "image", "inspect", tag]))
        if (
            len(rows) != 1
            or rows[0]["Config"].get("Labels", {}).get("lab.example.system") != CLUSTER
        ):
            raise RuntimeError("built image ownership label mismatch")
        images[tag] = rows[0]["Id"]
    save(path, images, replace=True)


def archive_data(receipt):
    archive = DATA / "archives" / receipt["owner"]
    archive.mkdir(parents=True, exist_ok=False)
    for path in list(DATA.iterdir()):
        if path.name not in {"archives", "cleanups"}:
            if path.is_symlink():
                raise RuntimeError("refusing symlinked active lab data")
            path.rename(archive / path.name)
    hashes = {}
    for path in sorted(archive.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("archived evidence contains a symlink")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[str(path.relative_to(archive))] = digest.hexdigest()
    save(archive / "manifest.json", {"owner": receipt, "sha256": hashes})
    return archive


def kind_down():
    owner()
    namespaces = kube("get", "namespace", "-o", "json")["items"]
    if any(row["metadata"]["name"] == "kafka" for row in namespaces):
        raise RuntimeError("run make down before deleting owned kind")
    receipt = json.loads((STATE / "replication-owner.json").read_text())
    inventory = STATE / "image-ownership.json"
    if inventory.exists():
        for tag, expected in json.loads(inventory.read_text()).items():
            rows = json.loads(call(["docker", "image", "inspect", tag]))
            if (
                len(rows) != 1
                or rows[0]["Id"] != expected
                or rows[0]["Config"].get("Labels", {}).get("lab.example.system")
                != CLUSTER
            ):
                raise RuntimeError(
                    "refusing cleanup of a changed/unowned image: " + tag
                )
        remaining = json.loads(inventory.read_text())
        for tag in list(remaining):
            call(["docker", "image", "rm", tag])
            del remaining[tag]
            save(inventory, remaining, replace=True)
    call(["kind", "delete", "cluster", "--name", CLUSTER], timeout=180)
    if CLUSTER in call(["kind", "get", "clusters"]).splitlines():
        raise RuntimeError("kind cluster still exists")
    archive = archive_data(receipt)
    save(
        DATA / "cleanups" / (receipt["owner"] + ".json"),
        {"owner": receipt, "archive": str(archive.relative_to(DATA))},
    )
    CONFIG.unlink(missing_ok=True)
    (STATE / "replication-owner.json").unlink()
    inventory.unlink(missing_ok=True)
