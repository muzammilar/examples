"""Bound image-copy demand before starting/loading the owned kind cluster."""
import json
from pathlib import Path
import subprocess
import sys
import time

NODE = "kindest/node@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
GIB = 1024**3


def main():
    mode = sys.argv[1]
    raw = subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--entrypoint", "df", NODE, "-Pk", "/"], text=True)
    free = int(raw.splitlines()[-1].split()[3]) * 1024
    images = {}
    if mode == "create":
        required = 40 * GIB
    elif mode == "load":
        for image in ("examples/spark-hdfs-orders:4.3.0", "examples/spark-hdfs-storage:3.5.0", "examples/spark-hdfs-verifier:26.0.1"):
            images[image] = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
        hadoop = images["examples/spark-hdfs-storage:3.5.0"]
        verifier = images["examples/spark-hdfs-verifier:26.0.1"]
        if verifier["RootFS"]["Layers"][:len(hadoop["RootFS"]["Layers"])] != hadoop["RootFS"]["Layers"]:
            raise ValueError("Verifier must share the exact Hadoop base before deduplicating the space estimate")
        # Three worker imports; Hadoop is already part of the verifier image.
        # Docker inspect Size undercounted imported content plus overlay snapshots
        # in the first run. Reserve a conservative 3x and keep 8 GiB headroom.
        required = 3 * 3 * (images["examples/spark-hdfs-orders:4.3.0"]["Size"] + verifier["Size"]) + 8 * GIB
    else:
        raise ValueError("Expected create or load")
    receipt = {"mode": mode, "available_bytes": free, "minimum_required_bytes": required,
               "image_sizes": {key: value["Size"] for key, value in images.items()},
               "estimate": "Conservative 3x image size for each of three worker imports plus 8 GiB for operator, data and headroom; create reserves 40 GiB."}
    Path("results").mkdir(exist_ok=True)
    Path(f"results/capacity-{mode}-{time.time_ns()}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    if free < required:
        raise SystemExit("Insufficient Docker VM space; no image loading or cleanup was performed")


if __name__ == "__main__":
    main()
