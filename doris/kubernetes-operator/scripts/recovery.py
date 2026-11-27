"""Verify retained member crash and controller restart without reloading data."""

import json
from pathlib import Path
import sys
import subprocess
import re
import time

import kube
import topology
import workload
import readiness


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n")


def main():
    mode = sys.argv[1]
    if mode not in ("backend", "operator"):
        raise ValueError(mode)
    revision = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    directory = kube.ROOT / "results" / f"{mode}-recovery-{time.time_ns()}"
    directory.mkdir(parents=True)
    topology.main()
    database = [
        p
        for p in kube.get("pods")["items"]
        if p["metadata"].get("labels", {}).get("lab.example/role")
        in ("frontend", "backend")
    ]
    before = {p["metadata"]["name"]: p for p in database}
    save(directory / "before-pods.json", before)
    claims = kube.get("pvc")["items"]
    before_claims = {p["metadata"]["name"]: p["metadata"]["uid"] for p in claims}
    save(directory / "before-claims.json", before_claims)
    started = time.monotonic()
    if mode == "operator":
        kube.run("rollout", "restart", "deployment/doris-operator")
        kube.run(
            "rollout",
            "status",
            "deployment/doris-operator",
            "--timeout=180s",
            timeout=195,
        )
    else:
        candidates = sorted(
            name
            for name, p in before.items()
            if p["metadata"]["labels"]["lab.example/role"] == "backend"
        )
        victim = candidates[0]
        old = before[victim]
        container = old["status"]["containerStatuses"][0]
        # Target the native containerd task from the Pod's exact runtime ID.
        # API deletion alone does not prove that the old process was fenced.
        node = old["spec"]["nodeName"]
        if node not in [
            "doris-kubernetes-operator-" + suffix
            for suffix in ("worker", "worker2", "worker3")
        ]:
            raise ValueError("Unexpected worker ownership")
        identifier = container["containerID"].removeprefix("containerd://")
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("Unexpected runtime container identity")
        native = json.loads(
            subprocess.check_output(
                ["docker", "exec", node, "crictl", "inspect", identifier], text=True
            )
        )
        if (
            native["status"]["labels"].get("io.kubernetes.pod.uid")
            != old["metadata"]["uid"]
        ):
            raise ValueError("Runtime task no longer belongs to the witnessed Pod")
        save(directory / "before-native-container.json", native)
        injection = subprocess.run(
            [
                "docker",
                "exec",
                node,
                "ctr",
                "--namespace",
                "k8s.io",
                "tasks",
                "kill",
                "--signal",
                "SIGKILL",
                identifier,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        save(
            directory / "injection.json",
            {
                "pod": victim,
                "pod_uid": old["metadata"]["uid"],
                "container_id": container["containerID"],
                "exit_code": injection.returncode,
                "stdout": injection.stdout,
                "stderr": injection.stderr,
            },
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            obj = kube.get("pod", victim)
            state = obj.get("status", {}).get("containerStatuses", [{}])[0]
            save(directory / ("observation-" + str(time.time_ns()) + ".json"), obj)
            terminated = state.get("lastState", {}).get("terminated", {})
            if (
                state.get("restartCount", 0) > container.get("restartCount", 0)
                and terminated.get("exitCode") == 137
                and state.get("ready")
            ):
                if obj["metadata"]["uid"] != old["metadata"]["uid"]:
                    raise ValueError(
                        "Unexpected Pod replacement during retained-container test"
                    )
                save(directory / "native-crash-proof.json", state)
                break
            time.sleep(1)
        else:
            raise TimeoutError("No observed SIGKILL and ready restarted container")
    after = {
        p["metadata"]["name"]: p
        for p in kube.get("pods")["items"]
        if p["metadata"].get("labels", {}).get("lab.example/role")
        in ("frontend", "backend")
    }
    if set(before) != set(after) or any(
        before[n]["metadata"]["uid"] != after[n]["metadata"]["uid"] for n in before
    ):
        raise ValueError("Database Pod identities changed unexpectedly")
    if mode == "operator" and any(
        before[n]["status"]["containerStatuses"][0]["restartCount"]
        != after[n]["status"]["containerStatuses"][0]["restartCount"]
        for n in before
    ):
        raise ValueError(
            "Controller restart unexpectedly restarted database containers"
        )
    if {
        p["metadata"]["name"]: p["metadata"]["uid"] for p in kube.get("pvc")["items"]
    } != before_claims:
        raise ValueError("Retained storage identity changed")
    readiness.main()
    for replica in range(3):
        workload.execute("verify", revision, replica)
    topology.main()
    save(
        directory / "result.json",
        {
            "passed": True,
            "mode": mode,
            "elapsed_s": time.monotonic() - started,
            "revision": revision,
            "input_reloaded": False,
            "database_pod_and_pvc_uids_unchanged": True,
            "scope": "Quiesced retained data; no continuous external writer or availability/RPO qualification",
        },
    )
    print(json.dumps({"recovery": mode, "passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
