"""Publish a fully checked synthetic-tail run without changing failed evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import experiment as bench
import ingestion
from journal_restore import fingerprint


def load(path):
    return json.loads(path.read_text())


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "journal-tail").resolve()):
        raise ValueError("select an owned journal-tail run")
    result, environment, fault = [
        load(root / name) for name in ("result.json", "environment.json", "fault.json")
    ]
    if not result["passed"] or not result["original_and_damaged_preserved"]:
        raise RuntimeError("tail scenario did not pass")
    for name, digest in environment["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    for name, digest in environment["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("oracle fixture changed")
    stages, images = {}, set()
    for stage, key, expected in (
        ("damaged", "damaged_startup", 2),
        ("validate", "read_only_validation", 0),
        ("repaired", "repaired_startup", 0),
    ):
        inspection = load(root / stage / "after-inspection.json")
        init = load(root / stage / "volume-initialization.json")
        metrics = result["native_jobs"][key]
        if (
            inspection["State"]["Running"]
            or inspection["State"]["ExitCode"] != expected
            or metrics["exit_code"] != expected
        ):
            raise RuntimeError("wrong native outcome: " + stage)
        if load(root / stage / "volume-export-fenced.json")["State"]["Running"]:
            raise RuntimeError("volume export was not fenced")
        images.add(inspection["Image"])
        metrics["native_process_cpu_percent_one_core_100"] = (
            100 * metrics["process_cpu_core_seconds"] / (metrics["wall_ns"] / 1e9)
        )
        metrics["container_lifetime_seconds"] = bench.container_seconds(inspection)
        metrics["block_io"] = bench.io_counters(
            metrics["cgroup"]["io.stat"], metrics["container_lifetime_seconds"]
        )
        metrics["block_io"]["rate_scope"] = (
            "Cumulative container counters divided by full container StartedAt-to-FinishedAt lifetime; counters captured just before exit. Native-child timing is narrower."
        )
        stages[stage] = {
            "image": inspection["Image"],
            "mounts": inspection["Mounts"],
            "initialization": init,
            "limits": {
                k: inspection["HostConfig"][k]
                for k in ("NanoCpus", "Memory", "MemorySwap")
            },
        }
        if stages[stage]["limits"] != {
            "NanoCpus": 10**9,
            "Memory": 4 * 1024**3,
            "MemorySwap": 4 * 1024**3,
        }:
            raise RuntimeError("native resource budgets differ")
    if len(images) != 1:
        raise RuntimeError("native jobs used different images")
    expected_damaged = dict(fault["original"])
    expected_damaged.update(fault["damaged_log"])
    if (
        fingerprint(sorted((root / "damaged/state").glob("journal*")))
        != expected_damaged
    ):
        raise RuntimeError("damaged evidence changed")
    if (
        result["native_jobs"]["read_only_validation"]["native_files"]
        != expected_damaged
    ):
        raise RuntimeError("validation modified native files")
    if (
        result["native_validated_prefix_bytes"]
        != fault["original"]["journal.log"]["bytes"]
        or result["fault_bytes_added"] != 3
    ):
        raise RuntimeError("repair was not the known original prefix")
    if (
        fingerprint(sorted((root / "repaired/state").glob("journal*")))
        != fault["original"]
    ):
        raise RuntimeError("repaired state differs from the original native files")
    image_info = json.loads(
        bench.capture(["docker", "image", "inspect", next(iter(images))], text=True)
    )[0]
    disk = {}
    for stage in stages:
        samples = load(root / stage / "samples.json")["disks"]
        settled = load(root / stage / "volume-export.json")
        disk[stage] = {
            "sampled_volume_bytes_max_including_exports": max(
                [s["logical_bytes"] for s in samples if "logical_bytes" in s]
                + [settled["logical_bytes"]]
            ),
            "settled_volume_bytes_including_exports": settled["logical_bytes"],
            "settled_volume_allocated_bytes_including_exports": settled[
                "allocated_bytes"
            ],
        }
    host_files = bench.storage_sample(root)
    for resource, flags in (
        ("ps", ["-aq"]),
        ("network", ["ls", "-q"]),
        ("volume", ["ls", "-q"]),
    ):
        if subprocess.check_output(
            [
                "docker",
                resource,
                *flags,
                "--filter",
                "label=com.docker.compose.project=rayforce-duckdb-journal",
            ],
            text=True,
        ).strip():
            raise RuntimeError(
                "owned resources remain after tail workflow: " + resource
            )
    ingestion.save(
        bench.ROOT / "projects/rayforce-journal/reports/TAIL_RECOVERY_RESULTS.json",
        {
            "source_directory": str(root.relative_to(bench.ROOT)),
            "result": result,
            "environment": environment,
            "fault": fault,
            "native_stages": stages,
            "runtime_image": {
                key: image_info[key] for key in ("Id", "Architecture", "Os")
            },
            "disk_observations": disk,
            "case_host_disk_after_cleanup": {
                "logical_bytes": host_files["logical_bytes"],
                "allocated_bytes": host_files["allocated_bytes"],
                "scope": "All retained state, exports and raw evidence in this case, after Linux volume deletion. Reported blocks are not exclusive physical extents; directory sampling can miss transient peaks. Source fixture/witness are outside this sum.",
            },
            "cleanup_verified": True,
            "unrun": [
                "interior corruption and rejected unsafe truncation",
                "interrupted live snapshot publication",
                "real device/power failure",
                "equivalent DuckDB recovery",
                "repeated capacity/cache-temperature comparison",
            ],
            "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    print("Published native torn-tail refusal, validation and guarded copy repair")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_directory", type=Path)
    publish(parser.parse_args().result_directory)
