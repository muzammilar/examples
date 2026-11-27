"""Characterize bounded fresh-read recovery, retaining every failed attempt."""
import datetime
import json
from pathlib import Path
import subprocess
import time

COMPOSE = ["docker", "compose", "--project-name", "doris-replicated-orders"]


def main():
    run = Path("results") / f"backend-recovery-{time.time_ns()}"
    run.mkdir(parents=True)
    started = time.monotonic()
    errors = 0
    result = None
    subprocess.run([*COMPOSE, "run", "--rm", "orders", "verify"], check=True)
    try:
        subprocess.run([*COMPOSE, "kill", "--signal", "SIGKILL", "be1"], check=True)
        raw = subprocess.check_output(["docker", "inspect", "doris-replicated-orders-be1-1"], text=True)
        state = json.loads(raw)[0]["State"]
        (run / "killed-state.json").write_text(json.dumps(state, indent=2) + "\n")
        if state["Running"] or state["ExitCode"] != 137:
            raise RuntimeError("Expected stopped BE with exit 137")
        fault_at = time.monotonic()
        deadline = fault_at + 90
        with (run / "read-attempts.jsonl").open("x") as history:
            while time.monotonic() < deadline:
                name = f"doris-replicated-orders-read-{time.time_ns()}"
                attempt_at = time.monotonic()
                child = subprocess.Popen([*COMPOSE, "run", "--rm", "--name", name, "orders", "verify"],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    out, err = child.communicate(timeout=max(0.1, deadline - time.monotonic()))
                    code = child.returncode
                except subprocess.TimeoutExpired:
                    subprocess.run(["docker", "stop", "--time", "5", name], check=False, capture_output=True)
                    out, err = child.communicate(timeout=10)
                    code = -1
                    err += "\nExternal 90-second phase deadline expired."
                record = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          "elapsed_since_fault_s": time.monotonic() - fault_at,
                          "duration_s": time.monotonic() - attempt_at, "code": code,
                          "stdout": out, "stderr": err}
                history.write(json.dumps(record) + "\n")
                history.flush()
                if code == 0:
                    result = json.loads(out)
                    break
                errors += 1
                if "wrong order" in err or "incomplete output" in err:
                    raise RuntimeError(f"Correctness violation; do not retry: {run}")
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        if result is None:
            raise RuntimeError(f"No exact fresh read within 90 seconds; evidence: {run}")
        state = json.loads(subprocess.check_output(
            ["docker", "inspect", "doris-replicated-orders-be1-1"], text=True))[0]["State"]
        if state["Running"]:
            raise RuntimeError("Fault was not held during successful read")
        receipt = {"errors_before_recovery": errors, "fresh_read_recovery_s": time.monotonic() - fault_at,
                   "successful_read": result, "backend_still_down": True,
                   "phase_budget_s": 90, "evidence": str(run)}
        (run / "outage-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt))
    finally:
        subprocess.run([*COMPOSE, "up", "--no-deps", "-d", "--wait", "be1"], check=True)
    subprocess.run(["make", "up"], check=True)
    subprocess.run(["make", "verify-replicas"], check=True)
    subprocess.run(["make", "topology"], check=True)
    (run / "completed.json").write_text(json.dumps({"complete": True, "elapsed_s": time.monotonic() - started,
                                                    "all_replica_readbacks": "passed", "native_topology": "passed"}, indent=2) + "\n")


if __name__ == "__main__":
    main()
