"""Observe owned Kubernetes node/Pod resource counters during a lifecycle action."""

import datetime
import json
import subprocess
import sys
import time

import kube


def main():
    target = sys.argv[1:]
    if not target:
        raise ValueError("Pass a Make target to observe")
    path = kube.ROOT / "results" / f"resources-{time.time_ns()}.jsonl"
    started = time.monotonic()
    with path.open("x") as history:
        child = subprocess.Popen(["make", *target], cwd=kube.ROOT, env=kube.ENV)
        try:
            while child.poll() is None:
                entry = {
                    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "elapsed_s": time.monotonic() - started,
                    "command": ["make", *target],
                    "nodes": [],
                }
                nodes = kube.get("nodes")
                for node in nodes["items"]:
                    name = node["metadata"]["name"]
                    if not name.startswith("doris-kubernetes-operator-"):
                        raise RuntimeError("Unexpected node in private context")
                    raw = kube.run(
                        "get",
                        "--raw",
                        "/api/v1/nodes/" + name + "/proxy/stats/summary",
                        check=False,
                        timeout=15,
                    )
                    if raw.returncode:
                        entry["nodes"].append(
                            {"node": name, "collector_error": raw.stderr}
                        )
                        continue
                    sample = json.loads(raw.stdout)
                    entry["nodes"].append(
                        {
                            "node": name,
                            "aggregate": sample["node"],
                            "pods": [
                                p
                                for p in sample["pods"]
                                if p["podRef"]["namespace"] == "doris-lab"
                            ],
                        }
                    )
                history.write(json.dumps(entry) + "\n")
                history.flush()
                time.sleep(1)
        finally:
            if child.poll() is None:
                child.terminate()
            status = child.wait()
    raise SystemExit(status)


if __name__ == "__main__":
    main()
