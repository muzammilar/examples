"""Observe native job state; optionally kill a worker after a completed checkpoint."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CTX = "kind-flink-kubernetes-operator"
ENV = dict(os.environ, KUBECONFIG=str(ROOT / "results/kubeconfig"))
KUBE = [str(ROOT / "results/tools/kubectl"), "--context", CTX, "-n", "flink-lab"]


def call(*args):
    return subprocess.check_output([*KUBE, *args], env=ENV, text=True)


def rest(path):
    code = ("import urllib.request; print(urllib.request.urlopen("
            + repr("http://orders-rest:8081" + path) + ", timeout=5).read().decode())")
    return json.loads(call("exec", "object-verifier", "--", "python", "-c", code))


def main():
    mode = sys.argv[1]
    if mode not in ("test", "task-recovery"):
        raise ValueError(mode)
    fault = None
    restored_seen = None
    started = time.monotonic()
    path = ROOT / "results" / (mode + ".jsonl")
    # A new attempt gets a new history, never overwrites a prior failed attempt.
    path = path.with_name(f"{mode}-{time.time_ns()}.jsonl")
    with path.open("x") as stream:
        while time.monotonic() - started < 900:
            obj = json.loads(call("get", "flinkdeployment", "orders", "-o", "json"))
            state = obj.get("status", {}).get("jobStatus", {}).get("state")
            pods = json.loads(call("get", "pods", "-o", "json"))
            observation = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           "elapsed_s": time.monotonic() - started,
                           "job": obj.get("status", {}), "pods": pods}
            # cgroup counters retain CPU seconds, memory bytes and per-device byte/IO counts.
            counters = {}
            for pod in pods["items"]:
                if pod.get("status", {}).get("phase") != "Running":
                    continue
                name = pod["metadata"]["name"]
                result = subprocess.run(
                    [*KUBE, "exec", name, "--", "sh", "-c",
                     "for f in cpu.stat memory.current memory.peak io.stat; do echo $f; cat /sys/fs/cgroup/$f; done"],
                    env=ENV, text=True, capture_output=True, timeout=15,
                )
                counters[name] = {"code": result.returncode, "out": result.stdout,
                                  "err": result.stderr}
            observation["cgroups"] = counters
            if state == "RUNNING":
                try:
                    job_id = obj["status"]["jobStatus"]["jobId"]
                    checkpoints = rest(f"/jobs/{job_id}/checkpoints")
                    observation["checkpoints"] = checkpoints
                    restored_seen = max(restored_seen or 0, checkpoints["counts"].get("restored", 0))
                    if mode == "task-recovery" and fault is None and checkpoints["counts"]["completed"] >= 1:
                        workers = [p for p in pods["items"]
                                   if p["metadata"].get("labels", {}).get("component") == "taskmanager"
                                   and p.get("status", {}).get("phase") == "Running"]
                        if not workers:
                            raise RuntimeError("No running worker available for injection")
                        victim = workers[0]["metadata"]["name"]
                        uid = workers[0]["metadata"]["uid"]
                        call("delete", "pod", victim, "--grace-period=0", "--force", "--wait=true")
                        fault = {"pod": victim, "uid": uid, "elapsed_s": time.monotonic() - started}
                        observation["injection"] = fault
                except subprocess.CalledProcessError as error:
                    # Preserve transport failures while the engine restarts; terminal native state still gates.
                    observation["rest_error"] = str(error)
            stream.write(json.dumps(observation) + "\n")
            stream.flush()
            if state == "FINISHED":
                if mode == "task-recovery" and fault is None:
                    raise RuntimeError("Job finished before fault injection")
                if mode == "task-recovery" and (restored_seen or 0) < 1:
                    job_id = obj["status"]["jobStatus"]["jobId"]
                    final_checkpoints = rest(f"/jobs/{job_id}/checkpoints")
                    restored_seen = final_checkpoints["counts"].get("restored", 0)
                    if restored_seen < 1:
                        raise RuntimeError("No native checkpoint restoration observed after worker loss")
                receipt = {"mode": mode, "native_checkpoint_restores": restored_seen, "native_state": state, "fault": fault,
                           "elapsed_s": time.monotonic() - started, "history": path.name}
                (ROOT / "results" / f"{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
                print(json.dumps(receipt))
                return
            if state in ("FAILED", "CANCELED"):
                raise RuntimeError(f"Native job {state}; history: {path}")
            time.sleep(5)
    raise TimeoutError(f"Job did not finish within 900 seconds; history: {path}")


if __name__ == "__main__":
    main()
