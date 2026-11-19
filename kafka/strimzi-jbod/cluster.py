"""Example-owned kind/context, resource admission and workload Job primitives."""

import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "results"
if not STATE.resolve().is_relative_to(ROOT):
    raise ValueError("results must remain in this example")
WITNESS = STATE / "witness"
RESULTS = STATE / "production"
CLUSTER = "kafka-strimzi-jbod"
CONTEXT = "kind-" + CLUSTER
NAMESPACE = "kafka"
CONFIG = STATE / "kubeconfig"
KUBE = ["kubectl", "--kubeconfig", str(CONFIG), "--context", CONTEXT, "-n", NAMESPACE]
ENV = os.environ | {
    "KUBECONFIG": str(CONFIG),
    "HELM_CONFIG_HOME": str(STATE / "helm/config"),
    "HELM_CACHE_HOME": str(STATE / "helm/cache"),
    "HELM_DATA_HOME": str(STATE / "helm/data"),
}
LABEL = "lab.example/owner"


def call(args, timeout=180, input_text=None, merge_stderr=False):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=ENV,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"{args}: exit {result.returncode}: {(result.stderr or '')[-6000:]} {result.stdout[-1000:]}"
        )
    return result.stdout


def save(path, value, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not replace and path.exists():
        temporary.unlink()
        raise FileExistsError(path)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def wait(check, description, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError(description + " timed out")


def json_kube(*args, timeout=180):
    return json.loads(call(KUBE + list(args), timeout=timeout))


def docker_node(name):
    rows = json.loads(call(["docker", "inspect", name], timeout=30))
    if len(rows) != 1:
        raise RuntimeError("ambiguous kind node")
    row = rows[0]
    labels = row["Config"].get("Labels", {})
    if labels.get("io.x-k8s.kind.cluster") != CLUSTER or labels.get(
        "io.x-k8s.kind.role"
    ) not in ("worker", "control-plane"):
        raise RuntimeError("unowned kind node: " + name)
    if row["Name"].lstrip("/") != name:
        raise RuntimeError("kind node name changed")
    return row


def owner():
    data = json.loads((STATE / "owner.json").read_text())
    if data["cluster"] != CLUSTER or data["context"] != CONTEXT:
        raise RuntimeError("wrong cluster owner record")
    for name, identity in data["nodes"].items():
        row = docker_node(name)
        if row["Id"] != identity or not row["State"]["Running"]:
            raise RuntimeError("owned kind node missing/stopped: " + name)
    control = docker_node(CLUSTER + "-control-plane")
    endpoint = call(
        KUBE
        + [
            "config",
            "view",
            "--minify",
            "-o",
            "jsonpath={.clusters[0].cluster.server}",
        ],
        timeout=30,
    )
    ports = control["NetworkSettings"]["Ports"].get("6443/tcp") or []
    expected = {"https://127.0.0.1:" + p["HostPort"] for p in ports} | {
        "https://localhost:" + p["HostPort"] for p in ports
    }
    if endpoint not in expected or endpoint != data["api_server"]:
        raise RuntimeError("kubeconfig endpoint is not the owned control plane")
    system = json_kube("get", "namespace", "kube-system", "-o", "json")
    if system["metadata"]["uid"] != data["kube_system_uid"]:
        raise RuntimeError("Kubernetes cluster identity changed")
    marker = call(
        ["docker", "exec", control["Id"], "cat", "/var/lab/witness/.owner"], timeout=30
    ).strip()
    if marker != data["owner"]:
        raise RuntimeError("external witness mount differs from this lab")
    return data


def kind_up():
    STATE.mkdir(exist_ok=True)
    WITNESS.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    if CLUSTER in call(["kind", "get", "clusters"], timeout=30).splitlines():
        if not (STATE / "owner.json").exists():
            raise RuntimeError(
                "existing cluster lacks this example ownership proof; refusing adoption"
            )
        owner()
        return
    if (STATE / "owner.json").exists() or list(WITNESS.glob("*/case.json")):
        raise RuntimeError(
            "missing cluster with retained ownership/ACK histories; diagnose before recreating"
        )
    identity = uuid.uuid4().hex
    marker = WITNESS / ".owner"
    if marker.exists():
        raise RuntimeError(
            "retained witness marker without cluster; preserve/retire it before setup"
        )
    with marker.open("x") as stream:
        stream.write(identity + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    template = (ROOT / "kind-config.yaml").read_text()
    rendered = template.replace("${WITNESS_ROOT}", json.dumps(str(WITNESS))).replace(
        "${CONTROL_ROOT}", json.dumps(str(RESULTS))
    )
    if "${" in rendered:
        raise RuntimeError("unexpanded kind configuration")
    generated = STATE / "kind-config.yaml"
    generated.write_text(rendered)
    save(
        STATE / "creation-intent.json",
        {
            "owner": identity,
            "cluster": CLUSTER,
            "context": CONTEXT,
            "kind_config_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "started": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    )
    call(
        [
            "kind",
            "create",
            "cluster",
            "--name",
            CLUSTER,
            "--config",
            str(generated),
            "--kubeconfig",
            str(CONFIG),
        ],
        timeout=600,
    )
    names = call(["kind", "get", "nodes", "--name", CLUSTER], timeout=30).splitlines()
    if len(names) != 4:
        raise RuntimeError("expected one control plane and three owned workers")
    nodes = {name: docker_node(name)["Id"] for name in names}
    endpoint = call(
        KUBE
        + ["config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"]
    )
    system = json_kube("get", "namespace", "kube-system", "-o", "json")
    save(
        STATE / "owner.json",
        {
            "owner": identity,
            "cluster": CLUSTER,
            "context": CONTEXT,
            "nodes": nodes,
            "api_server": endpoint,
            "kube_system_uid": system["metadata"]["uid"],
        },
    )
    owner()


def namespace():
    data = owner()
    existing = call(
        KUBE + ["get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json"]
    )
    if existing:
        found = json.loads(existing)
        if found["metadata"].get("labels", {}).get(LABEL) != data["owner"]:
            raise RuntimeError("namespace is not owned by this lab")
    else:
        call(
            KUBE + ["create", "-f", "-"],
            input_text=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": NAMESPACE, "labels": {LABEL: data["owner"]}},
                }
            ),
        )


def job(role, case_id, output=None, mode="verify"):
    data = owner()
    if mode not in ("verify", "diagnose"):
        raise ValueError("invalid verification mode")
    if len(case_id) != 32 or any(c not in "0123456789abcdef" for c in case_id):
        raise ValueError("invalid case ID")
    plan = json.loads((WITNESS / case_id / "case.json").read_text())
    if plan["run_id"] != case_id:
        raise RuntimeError("witness identity changed")
    if role not in ("producer", "reader", "verify"):
        raise ValueError("invalid workload role")
    if os.getuid() == 0:
        raise RuntimeError("use a non-root host UID for the witness Pods")
    image = (
        "kafka-strimzi-producer:dev"
        if role == "producer"
        else "kafka-strimzi-consumer:dev"
    )
    binary = (
        "/usr/local/bin/order-producer"
        if role == "producer"
        else "/usr/local/bin/order-consumer"
    )
    suffix = uuid.uuid4().hex[:8] if role == "verify" else ""
    name = f"{role}-{case_id[:16]}" + ("-" + suffix if suffix else "")
    case_path = "/var/lab/witness/" + case_id
    if role == "verify":
        if output is None or not output.resolve().is_relative_to(
            (RESULTS / "cases" / case_id).resolve()
        ):
            raise ValueError("verifier output must belong to this case")
        output.mkdir(parents=True, exist_ok=False)
        # Result mounts are under the separately exposed example control tree.
        relative = str(output.relative_to(RESULTS))
        volumes = [
            {"name": "witness", "hostPath": {"path": case_path, "type": "Directory"}},
            {
                "name": "output",
                "hostPath": {
                    "path": "/var/lab/control/" + relative,
                    "type": "Directory",
                },
            },
        ]
        mounts = [
            {"name": "witness", "mountPath": "/witness", "readOnly": True},
            {"name": "output", "mountPath": "/output"},
        ]
        gate = "/output/start"
        environment = [
            {"name": "MODE", "value": mode},
            {"name": "OUTPUT_DIR", "value": "/output"},
        ]
    else:
        volumes = [
            {"name": "witness", "hostPath": {"path": case_path, "type": "Directory"}}
        ]
        mounts = [{"name": "witness", "mountPath": "/witness"}]
        gate = "/witness/start-" + role
        environment = []
    environment.append({"name": "WITNESS_DIR", "value": "/witness"})
    script = 'for i in $(seq 1 300); do if [ -f "$1" ]; then exec "$2"; fi; sleep 1; done; echo startup-gate-timeout >&2; exit 1'
    spec = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {
                LABEL: data["owner"],
                "lab.example/case": case_id,
                "lab.example/role": role,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": plan["duration_seconds"] + 300,
            "template": {
                "metadata": {
                    "labels": {
                        LABEL: data["owner"],
                        "lab.example/case": case_id,
                        "lab.example/role": role,
                    }
                },
                "spec": {
                    "nodeName": CLUSTER + "-control-plane",
                    "tolerations": [
                        {
                            "key": "node-role.kubernetes.io/control-plane",
                            "operator": "Exists",
                            "effect": "NoSchedule",
                        }
                    ],
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "terminationGracePeriodSeconds": 30,
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
                            "command": ["sh", "-ec", script, "gate", gate, binary],
                            "env": environment,
                            "volumeMounts": mounts,
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {
                                    "cpu": "1" if role != "verify" else "2",
                                    "memory": "512Mi"
                                    if role == "producer"
                                    else (
                                        "2Gi"
                                        if role == "verify"
                                        and plan["minimum_messages"] > 1000000
                                        else "1Gi"
                                    ),
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
    call(KUBE + ["create", "-f", "-"], input_text=json.dumps(spec))
    pods = wait(
        lambda: json_kube("get", "pods", "-l", "job-name=" + name, "-o", "json")[
            "items"
        ],
        "workload pod creation",
    )
    if len(pods) != 1:
        raise RuntimeError("ambiguous workload pod")
    pod = pods[0]["metadata"]["name"]
    call(
        KUBE + ["wait", "pod/" + pod, "--for=condition=Ready", "--timeout=180s"],
        timeout=200,
    )
    expected = call(
        ["docker", "run", "--rm", "--entrypoint", "sha256sum", image, binary],
        timeout=30,
    ).split()[0]
    actual = call(
        KUBE + ["exec", pod, "-c", role, "--", "sha256sum", binary], timeout=30
    ).split()[0]
    if expected != actual:
        raise RuntimeError("stale workload image; no application start authorized")
    (
        output / "start" if role == "verify" else WITNESS / case_id / ("start-" + role)
    ).touch(exist_ok=False)
    return {
        "job": name,
        "pod": pod,
        "role": role,
        "binary_sha256": actual,
        "manifest": spec,
    }


def configure_kafka():
    """Retained setup must never remove a populated JBOD directory."""
    owner()
    existing = call(
        KUBE + ["get", "kafka", "kafka", "--ignore-not-found", "-o", "json"]
    )
    if existing:
        kafka = json.loads(existing)
        if kafka["spec"]["kafka"]["version"] != "4.3.2":
            raise RuntimeError("retained Kafka version differs from this example")
        for role in ["brokers", "controllers"]:
            pool = json_kube("get", "kafkanodepool", role, "-o", "json")
            expected = "broker" if role == "brokers" else "controller"
            if pool["spec"]["roles"] != [expected] or pool["spec"]["replicas"] != 3:
                raise RuntimeError("retained role topology differs from this example")
        print(
            "Retaining existing KafkaNodePools and their storage; no disk removal",
            flush=True,
        )
    else:
        call(KUBE + ["apply", "-f", "manifests/kafka-one-disk.yaml"])
    call(
        KUBE + ["wait", "kafka/kafka", "--for=condition=Ready", "--timeout=900s"],
        timeout=920,
    )


def build_images():
    files = []
    for directory in [ROOT / "producer", ROOT / "consumer"]:
        for path in directory.rglob("*"):
            if not path.is_file() or any(
                part in {"build", "target"}
                for part in path.relative_to(directory).parts
            ):
                continue
            if path.suffix in {
                ".cpp",
                ".hpp",
                ".proto",
                ".rs",
                ".toml",
                ".lock",
            } or path.name in {"Dockerfile", "CMakeLists.txt", ".dockerignore"}:
                files.append(path)
    files.append(ROOT / ".dockerignore")
    hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)
    }
    images = {
        "producer": "kafka-strimzi-producer:dev",
        "consumer": "kafka-strimzi-consumer:dev",
    }
    record_path = STATE / "build-images.json"
    previous = json.loads(record_path.read_text()) if record_path.exists() else None
    existing = {}
    for role, image in images.items():
        ids = call(
            [
                "docker",
                "image",
                "ls",
                "--quiet",
                "--no-trunc",
                "--filter",
                "reference=" + image,
            ]
        ).split()
        if len(set(ids)) == 1:
            info = json.loads(call(["docker", "image", "inspect", image]))[0]
            if info["Config"].get("Labels", {}).get("lab.example.system") == CLUSTER:
                existing[role] = info
    if (
        previous
        and previous["sources"] == hashes
        and set(existing) == set(images)
        and all(previous["images"][r]["id"] == existing[r]["Id"] for r in images)
    ):
        print(
            "Current source hashes and example-built images verified; no rebuild",
            flush=True,
        )
        return
    for role, image in images.items():
        args = ["docker", "build", "--tag", image]
        args += (
            ["producer"]
            if role == "producer"
            else ["--file", "consumer/Dockerfile", "."]
        )
        started = time.time_ns()
        log_path = STATE / f"build-{role}-{started}.log"
        with log_path.open("x") as log:
            result = subprocess.run(
                args,
                cwd=ROOT,
                env=ENV,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )
        if result.returncode:
            raise RuntimeError(
                "Image build failed; complete output: "
                + str(log_path)
                + "\n"
                + log_path.read_text()[-6000:]
            )
    built = {}
    for role, image in images.items():
        info = json.loads(call(["docker", "image", "inspect", image]))[0]
        if info["Config"].get("Labels", {}).get("lab.example.system") != CLUSTER:
            raise RuntimeError("image lacks ownership label")
        version = call(["docker", "run", "--rm", image, "--version"]).strip()
        if version != "2.12.1":
            raise RuntimeError("native client versions differ: " + version)
        binary = (
            "/usr/local/bin/order-producer"
            if role == "producer"
            else "/usr/local/bin/order-consumer"
        )
        digest = call(
            ["docker", "run", "--rm", "--entrypoint", "sha256sum", image, binary]
        ).split()[0]
        built[role] = {
            "id": info["Id"],
            "native_version": version,
            "binary_sha256": digest,
        }
    if record_path.exists():
        record_path.rename(STATE / ("build-images-" + str(time.time_ns()) + ".json"))
    save(record_path, {"sources": hashes, "images": built})


if __name__ == "__main__":
    STATE.mkdir(exist_ok=True)
    with (STATE / "lifecycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        action = sys.argv[1]
        if action == "kind-up":
            kind_up()
        elif action == "configure-kafka":
            configure_kafka()
        elif action == "build-images":
            build_images()
        elif action == "assert-owned":
            owner()
        elif action == "namespace":
            namespace()
        else:
            raise ValueError(action)
