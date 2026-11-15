"""Record resource caps and sampled usage around a single project's benchmark."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time

results = Path(os.environ["RESULTS_DIR"])
results.mkdir(parents=True, exist_ok=True)
compose = [
    "docker",
    "compose",
    "--profile",
    "cluster",
    "--profile",
    "single",
    "--profile",
    "sharded",
]


def capture(args):
    return subprocess.check_output(args, text=True).strip()


ids = capture(compose + ["ps", "-q"]).splitlines()
if not ids:
    raise RuntimeError("no running example services")
inspect = json.loads(capture(["docker", "inspect", *ids]))
caps = {
    x["Name"].lstrip("/"): {
        "nano_cpus": x["HostConfig"]["NanoCpus"],
        "memory_bytes": x["HostConfig"]["Memory"],
        "image": x["Config"]["Image"],
    }
    for x in inspect
}
if any(x["nano_cpus"] <= 0 or x["memory_bytes"] <= 0 for x in caps.values()):
    raise RuntimeError("all benchmark services require explicit CPU/memory caps")
stop = threading.Event()
samples = []
sampling_errors = []


def sample():
    while not stop.is_set():
        try:
            lines = capture(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids]
            )
            samples.append(
                {
                    "monotonic": time.monotonic(),
                    "containers": [json.loads(x) for x in lines.splitlines()],
                }
            )
        except Exception as error:
            sampling_errors.append(str(error))
        stop.wait(3)


thread = threading.Thread(target=sample)
thread.start()
started = datetime.now(timezone.utc).isoformat()
try:
    completed = subprocess.run(
        compose
        + [
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "tools",
            "benchmark",
        ]
    )
finally:
    stop.set()
    thread.join()
    report = {
        "started_utc": started,
        "host_machine": platform.machine(),
        "docker": json.loads(capture(["docker", "info", "--format", "{{json .}}"])),
        "caps": caps,
        "samples": samples,
        "sampling_errors": sampling_errors,
    }
    # Keep only non-sensitive Docker capacity fields.
    report["docker"] = {
        k: report["docker"][k]
        for k in ["Architecture", "NCPU", "MemTotal", "ServerVersion"]
    }
    if platform.system() == "Darwin":
        report["cpu_model"] = capture(["sysctl", "-n", "machdep.cpu.brand_string"])
    (results / "resources.json").write_text(json.dumps(report, indent=2) + "\n")
if completed.returncode:
    raise SystemExit(completed.returncode)
