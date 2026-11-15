import os
from pathlib import Path

mode = os.environ.get("MODE", "cluster")
if mode not in ["single", "cluster", "sharded"]:
    raise ValueError("MODE must be single, cluster or sharded")
prefix = os.environ.get("NETWORK_PREFIX", "172.30.61")
path = Path("config/generated")
path.mkdir(parents=True, exist_ok=True)
for group in range(3 if mode == "sharded" else 1):
    name = "nodes" if group == 0 else f"nodes{group}"
    count = 1 if mode == "single" else 3
    (path / name).write_text(
        ",".join(f"{prefix}.{11 + group * 3 + i}:8107:8108" for i in range(count))
        + "\n"
    )
