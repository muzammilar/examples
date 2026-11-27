"""Refuse a new lab when Docker VM free space cannot cover its image copies."""
import json
from pathlib import Path
import subprocess
import time

NODE_IMAGE = "kindest/node@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
MINIMUM_FREE = 16 * 1024**3


def main():
    text = subprocess.check_output(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "df",
         NODE_IMAGE, "-Pk", "/"], text=True,
    )
    available = int(text.splitlines()[-1].split()[3]) * 1024
    receipt = {"timestamp_ns": time.time_ns(), "available_bytes": available,
               "minimum_free_bytes": MINIMUM_FREE, "raw_df": text}
    results = Path("results")
    results.mkdir(exist_ok=True)
    (results / f"capacity-{time.time_ns()}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if available < MINIMUM_FREE:
        raise SystemExit(f"Docker VM has {available / 1024**3:.2f} GiB free; this three-node lab needs at least 16 GiB before setup. No cleanup was performed.")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
