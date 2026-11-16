"""Host-side, project-scoped SIGKILL while external journals keep running."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

engine = os.environ["ENGINE"]
mode = os.environ.get("MODE", "cluster")
if mode != "cluster" and not (engine in ("typesense", "vespa") and mode == "sharded"):
    raise SystemExit("failover requires MODE=cluster (or Typesense/Vespa MODE=sharded)")
projects = {
    "typesense": "typesense-product-search",
    "pgvector": "pgvector-product-search",
    "lancedb": "lancedb-versioned-product-search",
    "vespa": "vespa-phased-product-search",
}
project = projects[engine]
results = Path(os.environ.get("RESULTS_DIR", "results/cluster"))
results.mkdir(parents=True, exist_ok=True)
# Never erase an earlier failure's ACK oracle just to start another case.
if any(
    (results / file).exists()
    for file in ("churn.jsonl", "churn.json", "fault.json", "reconciliation.json")
):
    raise RuntimeError(
        "prior fault evidence exists; preserve and diagnose it before a new isolated lifecycle"
    )
compose = ["docker", "compose", "--profile", mode]


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def tools(action):
    run(
        compose
        + ["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "tools", action]
    )


run(compose + ["build", "tools"])
tools("stats")
stats = json.loads((results / "stats.json").read_text())
if engine == "typesense":
    leaders = [
        url for url, info in stats.items() if info.get("debug", {}).get("state") == 1
    ]
    if mode == "sharded":
        shard = int(os.environ.get("FAULT_SHARD", "0"))
        if not 0 <= shard < 3:
            raise ValueError("FAULT_SHARD must be 0..2")
        endpoints = {f"http://ts{shard * 3 + i}:8108" for i in range(1, 4)}
        leaders = [url for url in leaders if url in endpoints]
    if len(leaders) != 1:
        raise RuntimeError("expected one Raft leader: " + str(stats))
    service = leaders[0].split(":")[1].strip("/")
elif engine == "pgvector":
    leaders = [host for host, info in stats.items() if info.get("role") == "primary"]
    if len(leaders) != 1:
        raise RuntimeError("expected one PostgreSQL primary")
    service = leaders[0]
elif engine == "lancedb":
    service = os.environ.get("FAULT_SERVICE", "writer")
    if service not in ("writer", "reader1"):
        raise ValueError("FAULT_SERVICE must be writer or reader1")
else:
    service = "node0"
    if mode == "sharded":
        service = os.environ.get("FAULT_SERVICE", "content0")
        if service not in [f"content{i}" for i in range(9)]:
            raise ValueError("FAULT_SERVICE must be content0..content8")


def survivors():
    ids = run(compose + ["ps", "-q"], capture_output=True).stdout.split()
    values = json.loads(run(["docker", "inspect", *ids], capture_output=True).stdout)
    return {
        value["Config"]["Labels"]["com.docker.compose.service"]: (
            value["Id"],
            value["State"]["StartedAt"],
        )
        for value in values
        if value["Config"]["Labels"]["com.docker.compose.service"] != service
    }


before_survivors = survivors()

# No adoption/removal of an existing container with this name.
name = project + "-fault-client"
if (
    subprocess.run(
        ["docker", "container", "inspect", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode
    == 0
):
    raise RuntimeError("existing fault client; inspect it before rerunning")
client = (
    run(
        compose
        + [
            "run",
            "-d",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "tools",
            "churn",
        ],
        capture_output=True,
    )
    .stdout.strip()
    .splitlines()[-1]
)
killed = False
try:
    deadline = time.monotonic() + 90
    while True:
        journal = results / "churn.jsonl"
        if journal.exists() and journal.read_text().count('"stage": "ack"') >= 5:
            break
        state = run(
            ["docker", "inspect", "--format", "{{.State.Running}}", client],
            capture_output=True,
        ).stdout.strip()
        if state != "true" or time.monotonic() > deadline:
            raise RuntimeError("churn did not produce initial acknowledgements")
        time.sleep(1)
    run(compose + ["kill", "-s", "SIGKILL", service])
    killed = True
    fault = {
        "engine": engine,
        "service": service,
        "signal": "SIGKILL",
        "marker_scope": "after SIGKILL completed",
        "fault_monotonic": float(
            run(
                [
                    "docker",
                    "exec",
                    client,
                    "python",
                    "-c",
                    "import time; print(time.monotonic())",
                ],
                capture_output=True,
            ).stdout
        ),
        "fault_epoch": time.time(),
    }
    (results / "fault.json").write_text(json.dumps(fault, indent=2) + "\n")
    # Leave the failed process absent long enough to observe recovery or writer unavailability.
    down_seconds = int(os.environ.get("NODE_DOWN_SECONDS", "30"))
    if not 10 <= down_seconds <= 180:
        raise ValueError("NODE_DOWN_SECONDS must be 10..180")
    fault["requested_down_seconds"] = down_seconds
    until = time.monotonic() + down_seconds
    while time.monotonic() < until:
        time.sleep(1)
    fault["restart_monotonic"] = float(
        run(
            [
                "docker",
                "exec",
                client,
                "python",
                "-c",
                "import time; print(time.monotonic())",
            ],
            capture_output=True,
        ).stdout
    )
    start = time.monotonic()
    run(compose + ["up", "-d", "--wait", "--no-deps", service])
    killed = False
    fault["restart_health_seconds"] = time.monotonic() - start
    (results / "fault.json").write_text(json.dumps(fault, indent=2) + "\n")
    code = run(["docker", "wait", client], capture_output=True).stdout.strip()
    run(["docker", "logs", client])
    if code != "0":
        raise RuntimeError("fault client failed with exit " + code)
    if not (results / "churn.json").exists():
        raise RuntimeError("missing completed churn report")
    churn = json.loads((results / "churn.json").read_text())
    if churn["searches_succeeded"] == 0:
        raise RuntimeError("no successful searches under load")
    tools("reconcile")
    if engine == "vespa":
        run([sys.executable, "scripts/copies.py", "fault"])
    after_survivors = survivors()
    if before_survivors != after_survivors:
        raise RuntimeError(
            "a non-target service restarted during the isolated fault test"
        )
    fault["unchanged_surviving_services"] = sorted(after_survivors)
    (results / "fault.json").write_text(json.dumps(fault, indent=2) + "\n")
    report = json.loads((results / "reconciliation.json").read_text())
    if report["searches_while_node_absent"] == 0:
        raise RuntimeError("no searches succeeded while node was absent")
    if (engine != "lancedb" or service != "writer") and report[
        "acks_while_node_absent"
    ] == 0:
        raise RuntimeError("no writes resumed after fault")
finally:
    if killed:
        run(compose + ["up", "-d", "--wait", "--no-deps", service])
    # This client was created above and belongs exclusively to this invocation.
    run(["docker", "rm", "-f", client])
