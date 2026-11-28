"""Check the owned guest lab's host budget without touching host disks."""

import json
from pathlib import Path
import subprocess
import time

info = json.loads(
    subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
)
if (
    info["Architecture"] != "aarch64"
    or info["NCPU"] < 11
    or info["MemTotal"] < 22 * 1024**3
):
    raise RuntimeError(
        "This pinned ARM64 guest lab needs 11 CPUs and 22 GiB Docker RAM"
    )
space = json.loads(
    subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cpus",
            ".1",
            "--memory",
            "64m",
            "--entrypoint",
            "python3",
            "ceph-filesystem-ha-guests:21.2.0",
            "/opt/lab/capacity.py",
            "measure",
        ],
        text=True,
    )
)
Path("results").mkdir(exist_ok=True)
record = space | {
    k: info[k] for k in ("Architecture", "NCPU", "MemTotal", "ServerVersion")
}
record["required_free_bytes"] = 25 * 1024**3
record["passed"] = space["free_bytes"] >= record["required_free_bytes"]
Path("results", "preflight-" + str(time.time_ns()) + ".json").write_text(
    json.dumps(record, indent=2) + "\n"
)
print(json.dumps(record), flush=True)
if not record["passed"]:
    raise RuntimeError("Less than 25 GiB Docker free space; refusing guest startup")
Path("results/capacity.json").write_text(
    json.dumps(
        space
        | {k: info[k] for k in ("Architecture", "NCPU", "MemTotal", "ServerVersion")},
        indent=2,
    )
    + "\n"
)
