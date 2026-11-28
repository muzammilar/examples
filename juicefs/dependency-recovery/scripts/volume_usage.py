"""Read-only inventory of mounted owned volumes; not a physical-device meter."""

import json
from pathlib import Path

result = {}
for volume in Path("/storage").iterdir():
    apparent = allocated = files = vanished = 0
    for path in volume.rglob("*"):
        try:
            if path.is_file():
                stat = path.stat()
                apparent += stat.st_size
                allocated += stat.st_blocks * 512
                files += 1
        except FileNotFoundError:
            vanished += 1
    result[volume.name] = dict(
        apparent_bytes=apparent,
        allocated_bytes=allocated,
        files=files,
        concurrently_vanished_entries=vanished,
    )
result["_inventory_actor_cgroups"] = {
    name: (Path("/sys/fs/cgroup") / name).read_text()
    for name in ("cpu.stat", "memory.peak", "io.stat")
}
print(json.dumps(result))
