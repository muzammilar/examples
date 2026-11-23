"""Keep an observed integrity failure failed while publishing backup recovery."""

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
    if not root.is_relative_to((bench.RESULTS / "journal-corruption").resolve()):
        raise ValueError("select an owned journal-corruption case")
    result, environment = [
        load(root / name) for name in ("result.json", "environment.json")
    ]
    if (
        not result["diagnostic_completed"]
        or not result["integrity_verdict"].startswith("FAILED")
        or result["semantic_mismatches"]["wrong_bodies"] != 1
    ):
        raise RuntimeError("this report requires the recorded native integrity failure")
    for name, digest in environment["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    for name, digest in environment["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("independent input fixture differs")
    images, stages = set(), {}
    for name, metrics in result["native_jobs"].items():
        info = load(root / name / "after-inspection.json")
        if (
            info["State"]["Running"]
            or info["State"]["ExitCode"] != metrics["exit_code"]
            or load(root / name / "volume-export-fenced.json")["State"]["Running"]
        ):
            raise RuntimeError("native exit/export evidence differs")
        config = info["HostConfig"]
        if (
            config["NanoCpus"] != 10**9
            or config["Memory"] != 4 * 1024**3
            or config["MemorySwap"] != 4 * 1024**3
        ):
            raise RuntimeError("native budgets differ")
        images.add(info["Image"])
        metrics["native_process_cpu_percent_one_core_100"] = (
            100 * metrics["process_cpu_core_seconds"] / (metrics["wall_ns"] / 1e9)
        )
        metrics["container_lifetime_seconds"] = bench.container_seconds(info)
        metrics["block_io"] = bench.io_counters(
            metrics["cgroup"]["io.stat"], metrics["container_lifetime_seconds"]
        )
        metrics["block_io"]["rate_scope"] = (
            "Whole-container counters over StartedAt-to-FinishedAt lifetime; native child wall is narrower."
        )
        samples = load(root / name / "samples.json")["disks"]
        exported = load(root / name / "volume-export.json")
        stages[name] = {
            "image": info["Image"],
            "mounts": info["Mounts"],
            "initialization": load(root / name / "volume-initialization.json"),
            "sampled_state_bytes_max_including_exports": max(
                [s["logical_bytes"] for s in samples if "logical_bytes" in s]
                + [exported["logical_bytes"]]
            ),
            "settled_state_bytes": exported["logical_bytes"],
            "settled_allocated_bytes": exported["allocated_bytes"],
        }
    if len(images) != 1:
        raise RuntimeError("native image changed across jobs")
    for mode, expected in result["corrupt_native_files"].items():
        if fingerprint(sorted((root / mode / "state").glob("journal*"))) != expected:
            raise RuntimeError("corrupt evidence changed")
        if not result["faults"][mode]["tail_repair_rejected"]:
            raise RuntimeError("unsafe tail repair was not rejected")
    if (
        fingerprint(sorted((root / "backup/state").glob("journal*")))
        != result["original_files"]
    ):
        raise RuntimeError("backup restore altered native original files")
    if (
        result["backup_recovery_proof"]["acknowledged_loss"]
        or result["backup_recovery_proof"]["wrong_or_duplicate_bodies"]
    ):
        raise RuntimeError("backup recovery failed")
    dimension = bench.RESULTS / "data/customers.csv"
    result["semantic_customers"] = ingestion.verify_export(
        root / "valid-literal/state/restored-customers.csv",
        {
            "rows": 10000,
            "bytes": dimension.stat().st_size,
            "sha256": environment["fixture"]["sha256"]["customers.csv"],
        },
    )
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
            raise RuntimeError("owned resources remain after diagnostic: " + resource)
    image = json.loads(
        bench.capture(["docker", "image", "inspect", next(iter(images))], text=True)
    )[0]
    disk = bench.storage_sample(root)
    ingestion.save(
        bench.ROOT / "projects/rayforce-journal/reports/CORRUPTION_RESULTS.json",
        {
            "source_directory": str(root.relative_to(bench.ROOT)),
            "result": result,
            "environment": environment,
            "native_stages": stages,
            "runtime_image": {k: image[k] for k in ("Id", "Architecture", "Os")},
            "retained_case_host_disk": {
                "logical_bytes": disk["logical_bytes"],
                "allocated_bytes": disk["allocated_bytes"],
                "scope": "All preserved corrupt/backup files, exports and raw evidence after volume deletion; source fixture/witness separate, reported blocks not exclusive physical extents.",
            },
            "cleanup_verified": True,
            "scope": "FAILED under the specified logical payload-edit fault model, not a failed normal process-crash guarantee or physical bit-rot test. Backup recovery passed independently; it does not turn the integrity violation into a pass.",
            "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    print("Published FAILED integrity finding and independently passed backup recovery")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_directory", type=Path)
    publish(parser.parse_args().result_directory)
