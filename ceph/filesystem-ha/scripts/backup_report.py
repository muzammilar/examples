"""Curate one completed fresh-cluster restore; never include archived bodies."""

import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import lab


def load(path):
    return json.loads(path.read_text())


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def observed(folder, target):
    started = int(folder.name.rsplit("-", 1)[1])
    candidates = []
    for path in (lab.ROOT / "results").glob("resources-*.jsonl"):
        stamp = int(path.stem.rsplit("-", 1)[1])
        if not 0 <= started - stamp < 60 * 10**9:
            continue
        with path.open() as stream:
            header = json.loads(stream.readline())
        if target in header["command"]:
            candidates.append(path)
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one matching resource observation")
    path = candidates[0]
    value = json.loads(
        subprocess.check_output(
            [sys.executable, str(lab.ROOT / "scripts/resource_report.py"), str(path)],
            text=True,
        )
    )
    return value


def whole_device_counters(guest):
    result = {}
    for line in guest["diskstats"].splitlines():
        fields = line.split()
        name = fields[2]
        # Whole virtio OS disks and SCSI data/spare disks; never partitions or
        # device-mapper aliases, which would double-count the same underlying I/O.
        if not re.fullmatch(r"(?:vd|sd)[a-z]+", name):
            continue
        result[name] = {
            key: int(fields[index]) * scale
            for key, index, scale in [
                ("read_ios", 3, 1),
                ("write_ios", 7, 1),
                ("read_bytes", 5, 512),
                ("write_bytes", 9, 512),
                ("device_io_ms", 12, 1),
            ]
        }
    return result


def guest_io(before, after):
    result = {}
    for node, guest in after["guests"].items():
        previous = before["guests"][node]
        initial, final = whole_device_counters(previous), whole_device_counters(guest)
        same_epoch = previous["boot_id"] == guest["boot_id"]
        deltas = {}
        for device, values in final.items():
            if (
                same_epoch
                and device in initial
                and all(value >= initial[device][key] for key, value in values.items())
            ):
                deltas[device] = {
                    key: value - initial[device][key] for key, value in values.items()
                }
            else:
                deltas[device] = None
        result[node] = {
            "boot_id": guest["boot_id"],
            "same_boot": same_epoch,
            "whole_device_deltas": deltas,
        }
    return result


def main():
    manifest_path, create, restore = [Path(p).resolve() for p in sys.argv[1:]]
    for path in [manifest_path, create, restore]:
        path.relative_to((lab.ROOT / "results").resolve())
    manifest = load(manifest_path)
    source = manifest["source_fixture"]
    created = load(create / "status.json")
    if created.get("manifest") != str(manifest_path.relative_to(lab.ROOT)):
        raise RuntimeError("Backup phase belongs to another manifest")
    restored = load(restore / "status.json")
    if (
        not created["passed"]
        or not restored["passed"]
        or restored["source_fsid"] != source["fsid"]
        or restored["destination_fsid"] == source["fsid"]
    ):
        raise RuntimeError("Independent restore did not pass")
    if (
        restored["total_acks"] != manifest["total_acks"]
        or restored["acknowledged_bytes"] != manifest["total_bytes"]
    ):
        raise RuntimeError("Restore counts differ from saved cut")
    destination = next(
        load(p)
        for p in (lab.ROOT / "results").glob("retired-fixture-*.json")
        if load(p).get("fsid") == restored["destination_fsid"]
    )
    cleanup = {}
    for label, fixture in [("source", source), ("destination", destination)]:
        receipt = load(lab.ROOT / "results" / ("destroyed-" + fixture["id"] + ".json"))
        if (
            receipt["fixture"] != fixture["id"]
            or not receipt["containers_absent"]
            or not receipt["volume_absent"]
        ):
            raise RuntimeError("Owned state was not removed")
        cleanup[label] = receipt
    for row in manifest["entries"]:
        path = lab.ROOT / "results" / row["path"]
        path.resolve().relative_to(manifest_path.parent)
        if digest(path) != row["sha256"]:
            raise RuntimeError("Archive checksum changed")
    windows = {}
    for label, folder, target in [
        ("backup", create, "backup"),
        ("restore", restore, "restore"),
    ]:
        resources = observed(folder, target)
        storage = load(folder / "resources-after.json")
        before = load(folder / "resources-before.json")
        windows[label] = {
            "evidence": str(folder.relative_to(lab.ROOT)),
            "controller": load(folder / "duration.json"),
            "docker_resources": resources,
            "guest_disk_io": guest_io(before, storage),
            "native_storage": {
                k: storage[k]
                for k in ["df", "osd_perf", "data_pool_config", "metadata_pool_config"]
            },
            "qcow2_allocated_bytes": sum(
                p["allocated_bytes"] for p in storage["virtual_disks"]
            ),
            "guest_kernels": {
                node: {k: value[k] for k in ["kernel", "boot_id"]}
                for node, value in storage["guests"].items()
            },
        }
    result = {
        "classification": "Bounded file-level recovery; no throughput comparison or full metadata-backup qualification",
        "manifest": manifest,
        "manifest_sha256": digest(manifest_path),
        "restore": restored,
        "cleanup": cleanup,
        "archive_total_bytes": sum(row["archive_bytes"] for row in manifest["entries"]),
        "archive_allocated_bytes": sum(
            (lab.ROOT / "results" / row["path"]).stat().st_blocks * 512
            for row in manifest["entries"]
        ),
        "windows": windows,
        "limitations": [
            "Quiescent immutable ACK cut only; no ACL/xattr/hard-link/configuration or online-snapshot backup.",
            "Original containers and state volume were removed before creating the different destination FSID; only the rebuildable guest image was retained between runs.",
            "Archive and virtual disks remain on one physical computer; no off-site failure-domain or physical power-loss qualification.",
            "Native PG placement and fresh API readbacks are not independent raw-copy checksums or deep scrub.",
            "Outer Docker CPU includes QEMU emulation; host-controller and guest-process CPU scopes cannot be added to it.",
            "Workflow durations include verification/resource collection and exclude fresh guest provisioning; not production hardware RTO estimates.",
            "Compression is disabled; pool compression counters and file/archive/replicated bytes remain separate.",
            "Destination metadata pg_num read back 32 with pgp_num 8 despite bootstrap requesting eight PGs; source had 17 total PGs, destination 41. Creation-time autoscaling is suspected but not isolated. No matched-PG-layout performance comparison is claimed.",
        ],
        "application_source_sha256": digest(lab.ROOT / "files-workload/main.cpp"),
    }
    target = lab.ROOT / "reports" / ("FILE-RESTORE-" + manifest["backup"] + ".json")
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()
