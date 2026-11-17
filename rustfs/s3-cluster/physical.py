"""Read-only native slot census and retained immutable payload fingerprints.

RustFS remains the erasure/inline-metadata decoder. This does not infer shard
contents from an xl.meta filename or implement Reed-Solomon/bitrot decoding.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import workload as w


def formats(root=Path("/disks")):
    values = [
        json.loads((root / str(i) / "disk0/.rustfs.sys/format.json").read_text())
        for i in range(4)
    ]
    first = values[0]
    for i, value in enumerate(values):
        if (
            value["version"] != "1"
            or value["format"] != "xl"
            or value["xl"]["version"] != "3"
            or value["xl"]["distributionAlgo"] != "SIPMOD+PARITY"
        ):
            raise RuntimeError("unsupported pinned native format")
        sets = value["xl"]["sets"]
        if (
            value["id"] != first["id"]
            or sets != first["xl"]["sets"]
            or len(sets) != 1
            or len(sets[0]) != 4
            or len(set(sets[0])) != 4
            or value["xl"]["this"] != sets[0][i]
        ):
            raise RuntimeError(
                "native formats disagree or disk occupies wrong persisted slot"
            )
    return {
        "deployment": first["id"],
        "disks": {f"rustfs{i}": v["xl"]["this"] for i, v in enumerate(values)},
    }


def files(key, node, root=Path("/disks")):
    if key.startswith("/") or ".." in Path(key).parts:
        raise RuntimeError("invalid witnessed object path")
    directory = root / str(node) / "disk0" / w.BUCKET / key
    values = {}
    for path in directory.rglob("part.*"):
        if path.is_file() and re.fullmatch(r"part\.[0-9]+", path.name):
            with path.open("rb") as stream:
                values[path.relative_to(directory).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return values


def fingerprints(keys, root=Path("/disks")):
    result = {}
    for key in sorted(keys):
        # Overwrite/copy/multipart API cases may legitimately retire older data
        # directories. They retain SDK body and native protected-heal checks.
        if key.startswith("cases/"):
            continue
        parts = {str(i): files(key, i, root) for i in range(4)}
        if any(parts.values()):
            result[key] = parts
    return result


def missing_metadata(keys, root=Path("/disks")):
    return [
        [key, i]
        for key in keys
        for i in range(4)
        if not (root / str(i) / "disk0" / w.BUCKET / key / "xl.meta").is_file()
    ]


def main():
    mode = sys.argv[1]
    if mode not in ["snapshot", "wait"]:
        raise ValueError("unknown physical mode")
    _, required = w.Store.__new__(w.Store).expected(
        fault=(w.ROOT / "churn.jsonl").exists()
    )
    if not required:
        raise RuntimeError("no external ACK keys to audit")
    identity = formats()
    expected = w.load("native-membership")
    if identity != expected:
        raise RuntimeError(
            "read-only persisted slots differ from live native inventory"
        )
    if mode == "snapshot":
        missing = missing_metadata(required)
        if missing:
            raise RuntimeError("pre-fault metadata copy missing: " + str(missing[:5]))
        value = {"deployment": identity["deployment"], "parts": fingerprints(required)}
        print(
            json.dumps({"native-formats": identity, "physical-baseline": value}),
            flush=True,
        )
        return
    baseline = w.load("physical-baseline")
    if baseline["deployment"] != identity["deployment"]:
        raise RuntimeError("physical golden belongs to another native deployment")
    started = time.monotonic()
    while True:
        missing = missing_metadata(required)
        actual = fingerprints(baseline["parts"])
        changed = [
            key for key, value in baseline["parts"].items() if actual.get(key) != value
        ]
        if not missing and not changed:
            value = {
                "objects": len(required),
                "physical_metadata_copies": 4 * len(required),
                "retained_immutable_payload_files": sum(
                    len(p) for v in baseline["parts"].values() for p in v.values()
                ),
                "missing_metadata_copies": 0,
                "changed_payload_fingerprints": 0,
                "native_deployment": identity["deployment"],
                "wait_seconds": time.monotonic() - started,
                "docker_data_free_bytes": min(
                    os.statvfs(f"/disks/{i}").f_bavail
                    * os.statvfs(f"/disks/{i}").f_frsize
                    for i in range(4)
                ),
                "scope": "four metadata paths; every retained out-of-line immutable part file; inline decoding remains native",
            }
            print(
                json.dumps({"native-formats": identity, "physical-recovery": value}),
                flush=True,
            )
            return
        if time.monotonic() - started > 1200:
            print(
                json.dumps(
                    {
                        "native-formats": identity,
                        "physical-failure": {
                            "missing_metadata": missing,
                            "changed_payload_keys": changed,
                        },
                    }
                ),
                flush=True,
            )
            raise RuntimeError(
                "native physical census/fingerprint recovery deadline exceeded"
            )
        print(
            "Physical recovery pending:",
            len(missing),
            "metadata;",
            len(changed),
            "payload keys",
            flush=True,
            file=sys.stderr,
        )
        time.sleep(2)


if __name__ == "__main__":
    main()
