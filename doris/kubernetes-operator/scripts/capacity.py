"""Conservative free-space gate for the pinned, large Doris images."""

import json
from pathlib import Path
import subprocess
import sys
import time

NODE = "kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"


def main():
    mode = sys.argv[1]
    raw = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "df",
            NODE,
            "-Pk",
            "/",
        ],
        text=True,
    )
    free = int(raw.splitlines()[-1].split()[3]) * 1024
    # Docker's inspect Size is compressed content and undercounts imported snapshots.
    # Published FE+BE unpacked sizes are 3.85+8.22 GB, with shared layers; reserve
    # the full sum per worker rather than counting the shared-layer savings.
    required = {
        "create": 42 * 1024**3,
        "load": 40 * 1024**3,
        "worker": 16 * 1024**3,
        "run": 6 * 1024**3,
    }[mode]
    if mode in ("load", "worker"):
        nodes = [
            "doris-kubernetes-operator-" + suffix
            for suffix in ("worker", "worker2", "worker3")
        ]
        if mode == "worker":
            if len(sys.argv) != 3 or sys.argv[2] not in nodes:
                raise ValueError("Expected one owned worker")
            nodes = [sys.argv[2]]
        missing = 0
        for node in nodes:
            info = json.loads(
                subprocess.check_output(
                    ["docker", "exec", node, "crictl", "images", "-o", "json"],
                    text=True,
                )
            )
            tags = {
                tag for image in info["images"] for tag in image.get("repoTags", [])
            }
            if not {
                "docker.io/apache/doris:fe-4.0.8",
                "docker.io/apache/doris:be-4.0.8",
            }.issubset(tags):
                missing += 1
        if not missing:
            required = 6 * 1024**3
        elif mode == "load":
            required = (missing * 11 + 6) * 1024**3
    receipt = {
        "mode": mode,
        "available_bytes": free,
        "minimum_bytes": required,
        "observed_df": raw,
    }
    path = Path("results") / f"capacity-{mode}-{time.time_ns()}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    if free < required:
        raise SystemExit(
            "Insufficient Docker VM headroom; no cleanup or cluster mutation performed"
        )


if __name__ == "__main__":
    main()
