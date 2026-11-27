"""Read-only logical and allocated native storage usage from the audit container."""

import json
from pathlib import Path

result = {}
for kind in ("disks", "metadata"):
    for node in range(9):
        path = Path("/") / kind / str(node)
        files = [p.stat() for p in path.rglob("*") if p.is_file()]
        result[str(path)] = {
            "files": len(files),
            "apparent_bytes": sum(v.st_size for v in files),
            "allocated_bytes": sum(v.st_blocks * 512 for v in files),
        }
print(json.dumps(result))
