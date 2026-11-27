"""Crash one worker after a native checkpoint, then verify restored output."""
import datetime
import json
from pathlib import Path
import subprocess
import time

COMPOSE = ["docker", "compose", "--project-name", "flink-state-recovery"]


def run(*args):
    return subprocess.check_output([*COMPOSE, *args], text=True)


def api(path):
    return json.loads(run("exec", "-T", "jobmanager", "curl", "--max-time", "5",
                          "-fsS", "http://localhost:8081" + path))


def main():
    results = Path("results")
    results.mkdir(exist_ok=True)
    receipt = {"started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "verdict": "failed"}
    victim = None
    with (results / "recovery-sql.log").open("w") as log:
        child = subprocess.Popen([
            *COMPOSE, "run", "--rm", "sql", "-Drest.address=jobmanager",
            "-Djobmanager.rpc.address=jobmanager", "-f",
            "/opt/flink/lab-sql/stream-recovery.sql",
        ], stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Job exited before fault injection")
                jobs = [j for j in api("/jobs/overview")["jobs"] if j["state"] == "RUNNING"]
                if len(jobs) == 1:
                    job_id = jobs[0]["jid"]
                    checkpoints = api(f"/jobs/{job_id}/checkpoints")
                    if checkpoints["counts"]["completed"] >= 1:
                        receipt.update(job_id=job_id, before=checkpoints)
                        break
                time.sleep(1)
            else:
                raise TimeoutError("No completed checkpoint within 90 seconds")
            workers = run("ps", "-q", "taskmanager").split()
            if len(workers) != 2:
                raise RuntimeError("Expected two owned TaskManagers")
            victim = workers[0]
            subprocess.run(["docker", "kill", "--signal", "KILL", victim], check=True)
            state = json.loads(subprocess.check_output(
                ["docker", "inspect", victim, "--format", "{{json .State}}"], text=True))
            receipt["killed_state"] = state
            if state["Running"] or state["ExitCode"] != 137:
                raise RuntimeError("Worker crash was not observed")
            subprocess.run(["docker", "start", victim], check=True)
            if child.wait(timeout=240) != 0:
                raise RuntimeError("Streaming SQL failed after recovery")
            receipt["job"] = api(f"/jobs/{job_id}")
            if receipt["job"]["state"] != "FINISHED":
                raise RuntimeError(f"Native job failed despite SQL client status: {receipt['job']['state']}")
            receipt["after"] = api(f"/jobs/{job_id}/checkpoints")
            if receipt["after"]["counts"]["restored"] < 1:
                raise RuntimeError("Job did not restore a checkpoint")
            receipt["correctness"] = json.loads(run("run", "--rm", "verifier"))
            receipt["verdict"] = "passed"
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=15)
            if victim:
                subprocess.run(["docker", "start", victim], check=True)
            (results / "task-recovery.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
