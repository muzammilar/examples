"""Full native block bytes and exact native ring ownership, via read-only mounts."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import workload as w
from placement import load_ring, placement_errors


def physical_blocks():
    copies, corrupt = {}, []
    for node in range(9):
        root = Path(f"/disks/{node}")
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".zst" and re.fullmatch("[0-9a-f]{64}", path.stem):
                raise RuntimeError(
                    "compressed native blocks require separate qualification"
                )
            if not re.fullmatch("[0-9a-f]{64}", path.name):
                continue
            if path.relative_to(root).parts != (
                path.name[:2],
                path.name[2:4],
                path.name,
            ):
                raise RuntimeError(
                    "finalized native block is at the wrong on-disk path"
                )
            digest = hashlib.blake2b(path.read_bytes()).hexdigest()[:64]
            if digest != path.name:
                corrupt.append(str(path))
            copies.setdefault(path.name, []).append(node)
    return copies, corrupt


def expected_blocks():
    entries, required = w.Store.__new__(w.Store).expected(
        fault=(w.ROOT / "churn.jsonl").exists()
    )
    # A discarded response is not an ACK. Still require native copies for bodies
    # independently read back after that ambiguous completion.
    import json

    observations = [
        w.ROOT / "multipart-published.json",
        *w.ROOT.glob("fault-multipart-*/multipart-published.json"),
    ]
    for path in observations:
        if not path.exists():
            continue
        for value in json.loads(path.read_text()):
            if value != entries.get(value["key"]):
                raise RuntimeError(
                    "Multipart readback observation differs from witnessed attempt"
                )
            required.add(value["key"])
    blocks = set()
    for key in required:
        value = entries[key]
        source = "cases/overwrite" if key == "cases/copy" else key
        data = w.body(source, value["size"], value["version"])
        if hashlib.sha256(data).hexdigest() != value["sha256"]:
            raise RuntimeError(
                "body generator differs from the external witness: " + key
            )
        part_size = value.get(
            "multipart_part_size",
            8 * 1024 * 1024 if key == "cases/multipart" else max(len(data), 1),
        )
        if not isinstance(part_size, int) or part_size < 1:
            raise RuntimeError("Invalid multipart boundary in witness")
        for offset in range(0, len(data), part_size):
            part = data[offset : offset + part_size]
            for start in range(0, len(part), 1000000):
                blocks.add(
                    hashlib.blake2b(part[start : start + 1000000]).hexdigest()[:64]
                )
    return blocks


def main():
    mode = sys.argv[1]
    if mode not in ["snapshot", "wait"]:
        raise ValueError("unknown audit mode")
    peers = w.load("fixture")["peers"]
    ring = load_ring(peers)
    w.save("native-ring", ring)
    if mode == "snapshot":
        w.save("physical-baseline", sorted(expected_blocks()))
        return
    expected = set(w.load("physical-baseline"))
    if not expected:
        raise RuntimeError("no required native blocks")
    started = time.monotonic()
    while True:
        copies, corrupt = physical_blocks()
        missing, misplaced = placement_errors(expected, copies, ring)
        if not missing and not misplaced and not corrupt:
            report = {
                "baseline_blocks": len(expected),
                "physical_blocks": len(copies),
                "verified_copies": sum(len(v) for v in copies.values()),
                "required_native_copies_verified": 3 * len(expected),
                "missing_native_replicas": 0,
                "misplaced_live_blocks": 0,
                "corrupt_blocks": 0,
                "native_partitions": 256,
                "required_partitions_covered": len(
                    {bytes.fromhex(h)[0] for h in expected}
                ),
                "layout_version": ring["version"],
                "ring_sha256": ring["sha256"],
                "wait_seconds": time.monotonic() - started,
                "docker_data_free_bytes": min(
                    os.statvfs(f"/disks/{i}").f_bavail
                    * os.statvfs(f"/disks/{i}").f_frsize
                    for i in range(9)
                ),
            }
            w.save("physical-recovery", report)
            w.save("block-placement", copies)
            print(json.dumps(report, indent=2), flush=True)
            return
        if time.monotonic() - started > 1200:
            w.save(
                "physical-failure",
                {"missing": missing, "misplaced": misplaced, "corrupt": corrupt},
            )
            raise RuntimeError(
                f"native repair timeout: missing={len(missing)},misplaced={len(misplaced)},corrupt={len(corrupt)}"
            )
        print(
            "Native repair pending:",
            len(missing),
            "missing;",
            len(misplaced),
            "misplaced;",
            len(corrupt),
            "corrupt",
            flush=True,
        )
        time.sleep(5)


if __name__ == "__main__":
    main()
