"""Publish explicit storage controls and the preserved native rejection failure."""

import argparse
import hashlib
import json
from pathlib import Path

import experiment as bench
import ingestion


def publish(bind, volume, failed_native):
    cases = []
    images = set()
    for path, mode in ((bind, "bind"), (volume, "volume")):
        path = path.resolve(strict=True)
        if not path.is_relative_to((bench.RESULTS / "lock-capability").resolve()):
            raise ValueError("select owned filesystem-control cases")
        result = json.loads((path / "result.json").read_text())
        environment = json.loads((path / "environment.json").read_text())
        inspection = json.loads((path / "owner-inspection.json").read_text())
        if result["mode"] != mode or not result["finalized_after_cleanup"]:
            raise RuntimeError("wrong or incomplete filesystem control")
        for name, digest in environment["source_sha256"].items():
            if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("tested control source differs: " + name)
        images.add(inspection["Image"])
        cases.append(
            {
                "directory": str(path.relative_to(bench.ROOT)),
                "environment": environment,
                "result": result,
                "effective_limits": {
                    key: inspection["HostConfig"][key]
                    for key in ("NanoCpus", "Memory", "MemorySwap")
                },
                "mounts": inspection["Mounts"],
            }
        )
    if len(images) != 1:
        raise RuntimeError("controls used different images")
    if cases[0]["result"]["locks_enforced"] or not cases[1]["result"]["locks_enforced"]:
        raise RuntimeError(
            "this publication requires the observed bind failure and passing volume control"
        )
    failed_native = failed_native.resolve(strict=True)
    if not failed_native.is_relative_to((bench.RESULTS / "journal-live").resolve()):
        raise ValueError("failed native case must belong to this example")
    raw = (failed_native / "contender.log").read_text()
    attempts = [
        json.loads(line.removeprefix("__ATTEMPT__"))
        for line in raw.splitlines()
        if line.startswith("__ATTEMPT__")
    ]
    if (
        not attempts
        or attempts[0]["returncode"] != 0
        or attempts[0]["rejected_by_lock"]
    ):
        raise RuntimeError("missing native unexpected-open proof")
    ingestion.save(
        bench.ROOT / "projects/rayforce-journal/reports/FENCING_ENVIRONMENT_RESULTS.json",
        {
            "image_id": next(iter(images)),
            "filesystem_controls": cases,
            "failed_native_case": {
                "directory": str(failed_native.relative_to(bench.ROOT)),
                "attempts": attempts,
                "verdict": "FAILED: native second opener unexpectedly acquired the bind-mounted journal. The scenario stopped, so no final ACK/body reconciliation or concurrency qualification is claimed for this run.",
            },
            "scope": "Observed storage semantics on this host/runtime only. The host-share mount reports fakeowner; Docker-managed volume reports ext4. Standard-library controls show the bind-mount lock failure independently of Rayforce. Do not generalize to every bind mount or OS.",
        },
    )
    print("Published failed bind-mount ownership and passing ext4 controls")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bind_case", type=Path)
    parser.add_argument("volume_case", type=Path)
    parser.add_argument("failed_native_case", type=Path)
    args = parser.parse_args()
    publish(args.bind_case, args.volume_case, args.failed_native_case)
