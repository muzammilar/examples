"""Check the owned guest lab's host budget without touching host disks."""

import json
from pathlib import Path
import subprocess

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
if space["free_bytes"] < 25 * 1024**3:
    raise RuntimeError("Less than 25 GiB Docker free space; refusing guest startup")
Path("results").mkdir(exist_ok=True)
Path("results/capacity.json").write_text(
    json.dumps(
        space
        | {k: info[k] for k in ("Architecture", "NCPU", "MemTotal", "ServerVersion")},
        indent=2,
    )
    + "\n"
)
