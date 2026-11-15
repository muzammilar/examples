"""Run one shared S3 benchmark and record the actual Docker resource limits."""

import json
import os
from pathlib import Path
import subprocess

root = Path(os.environ.get("RESULTS_DIR", "results/verified"))
root.mkdir(parents=True, exist_ok=True)
ids = subprocess.check_output(["docker", "compose", "ps", "-q"], text=True).split()
containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
(root / "resource-limits.json").write_text(
    json.dumps(
        [
            {
                "name": c["Name"],
                "image": c["Config"]["Image"],
                "cpus": c["HostConfig"]["NanoCpus"] / 1e9,
                "memory_bytes": c["HostConfig"]["Memory"],
            }
            for c in containers
        ],
        indent=2,
    )
    + "\n"
)
with (root / "benchmark.log").open("w") as log:
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "tools",
            "benchmark",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )
print((root / "benchmark.json").read_text())
