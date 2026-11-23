"""Bound this sparse-disk lab without filling the shared Docker filesystem."""

import json
import os
import subprocess
import sys
from pathlib import Path

GIB = 1024**3
RUNTIME_FLOOR = 10 * GIB


def measure(path):
    value = os.statvfs(path)
    return {
        "free_bytes": value.f_bavail * value.f_frsize,
        "total_bytes": value.f_blocks * value.f_frsize,
    }


def require_free(value, minimum):
    if value["free_bytes"] < minimum:
        raise RuntimeError(
            f"Docker disk safety floor: {value['free_bytes']} < {minimum} bytes"
        )


def preflight(info, space):
    if info["Architecture"] != "aarch64":
        raise RuntimeError("the pinned Ceph guest/packages require ARM64")
    if info["MemTotal"] < 22 * GIB or info["NCPU"] < 11:
        raise RuntimeError(
            "the independent-observer lab requires 11 CPUs / 22 GiB Docker RAM"
        )
    require_free(space, 25 * GIB)


if __name__ == "__main__":
    if sys.argv[1:] == ["measure"]:
        print(json.dumps(measure("/")))
        raise SystemExit(0)
    if sys.argv[1:]:
        raise SystemExit("usage: capacity.py [measure]")
    project = os.environ.get("COMPOSE_PROJECT_NAME", "ceph-s3-cluster")
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
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
                "0.1",
                "--memory",
                "64m",
                "--label",
                "com.muzammilar.examples.capacity-check=true",
                "--entrypoint",
                "python3",
                project + "-guests:dev",
                "/opt/lab/capacity.py",
                "measure",
            ],
            text=True,
        )
    )
    preflight(info, space)
    result = space | {
        k: info[k] for k in ["Architecture", "NCPU", "MemTotal", "ServerVersion"]
    }
    root = Path(os.environ.get("RESULTS_DIR", "results/recovery"))
    root.mkdir(parents=True, exist_ok=True)
    (root / "capacity-before-boot.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
