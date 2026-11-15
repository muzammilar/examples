"""Require both a successful container exit and a fresh numeric benchmark artifact."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

project = os.environ["COMPOSE_PROJECT_NAME"]
name = project + "-benchmark"
root = Path("results")
artifact = root / "query-benchmark.json"
if artifact.exists():
    archive = root / (
        "query-benchmark-before-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
        + ".json"
    )
    artifact.rename(archive)
started = datetime.now(timezone.utc)
result = subprocess.run(
    [
        "docker",
        "compose",
        "run",
        "--name",
        name,
        "-T",
        "vector-workload",
        "benchmark",
    ],
    check=False,
)
info = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
if info["Config"]["Labels"].get("com.docker.compose.project") != project:
    raise RuntimeError("foreign benchmark container")
report = {
    "started_at": started.isoformat(),
    "image": info["Image"],
    "cli_exit_code": result.returncode,
    "container_state": info["State"],
    "artifact_present": artifact.is_file(),
}
(root / "benchmark-exit.json").write_text(json.dumps(report, indent=2) + "\n")
if (
    result.returncode
    or info["State"]["Status"] != "exited"
    or info["State"]["OOMKilled"]
    or info["State"]["ExitCode"]
    or not artifact.is_file()
):
    raise RuntimeError(
        "benchmark did not produce verified results; retain container and benchmark-exit.json for diagnosis"
    )
data = json.loads(artifact.read_text())
if len(data) != 16 or any(
    v["errors"] != 0
    or v["queries"] != int(os.environ["QUERIES"])
    or v["warmup_requests"] != 1
    for v in data.values()
):
    raise RuntimeError("invalid benchmark measurements")
subprocess.run(["docker", "rm", name], check=True)
