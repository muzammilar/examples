"""Read-only physical replica audit for Garage v2.5.0, uncompressed blocks.

Native source util/data.rs uses the first 32 bytes of BLAKE2b-512 (not BLAKE2b-256).
Only finalized 64-hex block filenames are examined. This does not implement placement.
"""

import hashlib
import json
from pathlib import Path
import re
import sys
import time
import workload as w


def audit():
    copies = {}
    corrupt = []
    for node in range(9):
        for p in Path(f"/disks/{node}").rglob("*"):
            if not p.is_file() or not re.fullmatch("[0-9a-f]{64}", p.name):
                continue
            digest = hashlib.blake2b(p.read_bytes()).hexdigest()[:64]
            if digest != p.name:
                corrupt.append(str(p))
            copies.setdefault(p.name, []).append(node)
    return copies, corrupt


out = Path("/results")
mode = sys.argv[1]
if mode == "snapshot":
    store = w.Store.__new__(w.Store)
    entries, required = store.expected(fault=(out / "churn.jsonl").exists())
    expected = set()
    for key in required:
        value = entries[key]
        source_key = "cases/overwrite" if key == "cases/copy" else key
        data = w.body(source_key, value["size"], value["version"])
        # Multipart upload part boundaries are independent block streams.
        part_size = 8 * 1024 * 1024 if key == "cases/multipart" else max(len(data), 1)
        for offset in range(0, len(data), part_size):
            part = data[offset : offset + part_size]
            for pos in range(0, len(part), 1000000):
                expected.add(
                    hashlib.blake2b(part[pos : pos + 1000000]).hexdigest()[:64]
                )
    (out / "physical-baseline.json").write_text(json.dumps(sorted(expected)) + "\n")
else:
    expected = set(json.loads((out / "physical-baseline.json").read_text()))
    start = time.monotonic()
    while True:
        copies, corrupt = audit()
        incomplete = [
            h for h in expected if len({n // 3 for n in copies.get(h, [])}) != 3
        ]
        if not corrupt and not incomplete:
            report = {
                "baseline_blocks": len(expected),
                "physical_blocks": len(copies),
                "verified_copies": sum(len(v) for v in copies.values()),
                "missing_zone_replicas": 0,
                "corrupt_blocks": 0,
                "wait_seconds": time.monotonic() - start,
            }
            (out / "physical-recovery.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            print(report, flush=True)
            break
        if time.monotonic() - start > 1200:
            raise RuntimeError(
                f"repair timeout: {len(incomplete)} blocks without three zones, corrupt={corrupt[:5]}"
            )
        print(f"Waiting for {len(incomplete)} blocks to regain three zones", flush=True)
        time.sleep(10)
