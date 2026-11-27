"""Kill a worker hosting an active task, then verify native failure and recomputation."""
import json
from pathlib import Path
import subprocess
import time

COMPOSE = ["docker", "compose", "--project-name", "spark-order-recovery"]
RESULTS = Path("results")


def output(*args):
    return subprocess.check_output(args, text=True)


def rest(path):
    script = "import urllib.request; print(urllib.request.urlopen(" + repr("http://orders:4040/api/v1" + path) + ",timeout=2).read().decode())"
    result = subprocess.run([*COMPOSE, "exec", "-T", "master", "python3", "-c", script], capture_output=True, text=True, timeout=10)
    with (RESULTS / "driver-rest-probes.jsonl").open("a") as log:
        log.write(json.dumps({"ts_ns": time.time_ns(), "path": path,
                              "code": result.returncode, "body": result.stdout,
                              "error": result.stderr}) + "\n")
    if result.returncode or not result.stdout.strip() or result.stdout.strip() == "Spark is starting up. Please wait a while until it's ready.":
        return None
    return json.loads(result.stdout)


def main():
    RESULTS.mkdir(exist_ok=True)
    run = RESULTS / f"worker-recovery-{time.time_ns()}"
    run.mkdir()
    containers = output(*COMPOSE, "ps", "-q", "worker").split()
    workers = {}
    for container in containers:
        info = json.loads(output("docker", "inspect", container))[0]
        for network in info["NetworkSettings"]["Networks"].values():
            workers[network["IPAddress"]] = container
    if len(containers) != 2:
        raise RuntimeError("Expected exactly two running owned workers")
    workload_name = f"spark-order-recovery-run-{time.time_ns()}"
    victim = None
    started = time.monotonic()
    with (run / "orders.log").open("x") as log:
        child = subprocess.Popen([*COMPOSE, "run", "--rm", "--name", workload_name, "--use-aliases", "orders"], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = started + 180
            while child.poll() is None and time.monotonic() < deadline:
                apps = rest("/applications")
                if apps:
                    app = apps[0]["id"]
                    executors = rest(f"/applications/{app}/executors")
                    if executors:
                        for executor in executors:
                            ip = executor["hostPort"].rsplit(":", 1)[0]
                            if executor["id"] != "driver" and executor["activeTasks"] > 0 and ip in workers:
                                victim = workers[ip]
                                (run / "active-executors.json").write_text(json.dumps(executors, indent=2) + "\n")
                                subprocess.run(["docker", "kill", "--signal", "KILL", victim], check=True)
                                info = json.loads(output("docker", "inspect", victim))[0]
                                (run / "killed-worker.json").write_text(json.dumps(info, indent=2) + "\n")
                                if info["State"]["Running"] or info["State"]["ExitCode"] != 137:
                                    raise RuntimeError("Expected an observed SIGKILL worker exit")
                                subprocess.run(["docker", "start", victim], check=True)
                                break
                    if victim:
                        break
                time.sleep(0.5)
            if victim is None:
                raise RuntimeError("No active task was observed before injection deadline/job completion")
            code = child.wait(timeout=300)
            if code:
                raise RuntimeError(f"Spark application failed: {code}; evidence: {run}")
            # Persist native event history outside the volume before teardown.
            raw = output(*COMPOSE, "exec", "-T", "master", "python3", "-c",
                         "import pathlib; print(''.join(p.read_text() for p in pathlib.Path('/data/events').rglob('*') if p.is_file() and not p.name.startswith('.')))")
            (run / "events.jsonl").write_text(raw)
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            removed = [e for e in events if e.get("Event") == "SparkListenerExecutorRemoved"]
            failed = [e for e in events if e.get("Event") == "SparkListenerTaskEnd" and e.get("Task End Reason", {}).get("Reason") != "Success"]
            if not removed or not failed:
                raise RuntimeError("Missing native executor-loss/task-failure evidence")
            receipt = {"victim": victim, "elapsed_s": time.monotonic() - started,
                       "executor_removals": len(removed), "failed_task_attempts": len(failed),
                       "native_removals": removed, "application_exit": code,
                       "independent_readback": "Required separately by Make verify",
                       "evidence": str(run)}
            (run / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            print(json.dumps(receipt))
        finally:
            if victim:
                subprocess.run(["docker", "start", victim], check=False, capture_output=True)
            if child.poll() is None:
                subprocess.run(["docker", "stop", "--time", "10", workload_name], check=False, capture_output=True)
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    main()
