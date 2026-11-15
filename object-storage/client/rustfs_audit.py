"""Read-only physical census; native deep heal validates coding/identity semantics.

For the replaced shard position, retain external raw part-file SHA-256 before
failure and compare reconstructed bytes afterwards. Do not decode xl.meta or
reimplement Reed-Solomon. Native protected-write receipts cover inline payloads.
"""

import hashlib
import json
from pathlib import Path
import sys
import re
import workload as w

out = Path("/results")
store = w.Store.__new__(w.Store)
entries, required = store.expected(fault=(out / "churn.jsonl").exists())
mode = sys.argv[1]
missing = []
parts = {}
for key in sorted(required):
    for disk in range(4):
        path = Path(f"/disks/{disk}") / w.BUCKET / key
        if not (path / "xl.meta").is_file():
            missing.append([key, disk])
        if disk == 0:
            # File-format semantics remain the server's responsibility. This is
            # only a byte fingerprint of existing native data shard files.
            parts[key] = {
                p.relative_to(path).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in path.rglob("part.*")
                if p.is_file() and re.fullmatch(r"part\.[0-9]+", p.name)
            }
if missing:
    raise RuntimeError(
        f"{len(missing)} missing physical object metadata copies: {missing[:5]}"
    )
if mode == "snapshot":
    (out / "physical-baseline.json").write_text(json.dumps(parts, indent=2) + "\n")
else:
    baseline = json.loads((out / "physical-baseline.json").read_text())
    different = [key for key, value in baseline.items() if parts.get(key) != value]
    if different:
        raise RuntimeError(
            "reconstructed raw shard fingerprints differ: " + str(different[:5])
        )
    report = {
        "objects": len(required),
        "physical_metadata_copies": 4 * len(required),
        "baseline_shard_fingerprints": sum(len(v) for v in baseline.values()),
        "missing_shards": 0,
        "changed_shard_fingerprints": 0,
    }
    (out / "physical-recovery.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report, flush=True)
