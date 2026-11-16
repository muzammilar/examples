"""Require native process-exit evidence before replacing an owned Neon PVC."""

import json
import subprocess
import time


def prove_stopped(original, timeout=60):
    node = original["spec"]["nodeName"]
    owner = json.loads(
        subprocess.check_output(["docker", "inspect", node], text=True, timeout=15)
    )[0]
    if owner["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "neon-storage":
        raise RuntimeError("refusing foreign node")
    cid = original["status"]["containerStatuses"][0]["containerID"].removeprefix(
        "containerd://"
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", node, "crictl", "inspect", cid],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if result.returncode:
            # Docker/transport errors are not process fencing. Even a NotFound
            # response needs a healthy CRI listing confirming this ID is absent.
            if (
                "notfound" not in result.stderr.lower()
                and "not found" not in result.stderr.lower()
            ):
                raise RuntimeError("cannot prove old process stopped: " + result.stderr)
            listing = json.loads(
                subprocess.check_output(
                    [
                        "docker",
                        "exec",
                        node,
                        "crictl",
                        "ps",
                        "-a",
                        "--id",
                        cid,
                        "-o",
                        "json",
                    ],
                    text=True,
                    timeout=15,
                )
            )
            if listing["containers"]:
                raise RuntimeError(
                    "CRI inspection failed but old container still exists"
                )
            state = "absent-from-healthy-cri"
        else:
            state = json.loads(result.stdout)["status"]["state"]
        if state in (
            "CONTAINER_EXITED",
            "absent-from-healthy-cri",
        ):
            return {
                "old_container_id": cid,
                "node": node,
                "old_process_stopped": True,
                "native_state": state,
            }
        if state != "CONTAINER_RUNNING":
            raise RuntimeError("unknown CRI state; refusing fencing claim: " + state)
        time.sleep(1)
    raise RuntimeError("old process still running; refusing cache replacement")
