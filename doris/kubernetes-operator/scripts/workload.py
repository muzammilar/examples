"""Submit bounded, example-owned Go workload Jobs with immutable run evidence."""

import json
from pathlib import Path
import sys
import time

import kube


def execute(mode, revision=1, replica=None):
    if mode not in ("setup", "seed", "mutate", "verify", "drop"):
        raise ValueError(mode)
    directory = kube.ROOT / "results" / f"{mode}-{time.time_ns()}"
    directory.mkdir(parents=True)
    env = [
        {"name": "DORIS_ADDRESS", "value": "orders-fe-service:9030"},
        {"name": "EXPECTED_REVISION", "value": str(revision)},
    ]
    if replica is not None:
        env.append({"name": "REPLICA_INDEX", "value": str(replica)})
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"generateName": "orders-" + mode + "-"},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 360,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "orders-workload",
                            "image": "examples/doris-operator-orders:4.0.8",
                            "imagePullPolicy": "Never",
                            "args": [mode],
                            "env": env,
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "128Mi"},
                                "limits": {"cpu": "1", "memory": "256Mi"},
                            },
                        }
                    ],
                }
            },
        },
    }
    obj = json.loads(
        kube.run("create", "-f", "-", "-o", "json", data=json.dumps(job)).stdout
    )
    name = obj["metadata"]["name"]
    started = time.monotonic()
    while time.monotonic() - started < 370:
        observed = kube.get("job", name)
        logs = kube.run("logs", "job/" + name, check=False).stdout
        (directory / "job.log").write_text(logs)
        status = observed.get("status", {})
        if status.get("failed"):
            (directory / "failed-job.json").write_text(
                json.dumps(observed, indent=2) + "\n"
            )
            raise RuntimeError("Workload failed; retain " + str(directory))
        if status.get("succeeded"):
            (directory / "receipt.json").write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "revision": revision,
                        "replica_index": replica,
                        "elapsed_s": time.monotonic() - started,
                        "job": observed,
                    },
                    indent=2,
                )
                + "\n"
            )
            print(
                json.dumps(
                    {"workload": mode, "passed": True, "evidence": str(directory)}
                )
            )
            return
        time.sleep(1)
    raise TimeoutError("Workload did not complete; preserve " + str(directory))


if __name__ == "__main__":
    execute(
        sys.argv[1],
        int(sys.argv[2]) if len(sys.argv) > 2 else 1,
        int(sys.argv[3]) if len(sys.argv) > 3 else None,
    )
