"""Publish native segment diagnostics without weakening the separate integrity verdict."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

import aof
import aof_segments
import experiment as bench
import ingestion
from journal_restore import fingerprint


def load(path):
    return json.loads(path.read_text())


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "aof-segments").resolve()):
        raise ValueError("select an owned aof-segments result directory")
    result, env = load(root / "result.json"), load(root / "environment.json")
    if not result["diagnostic_passed"] or (root / "failure.json").exists():
        raise RuntimeError("native segment diagnostic did not pass")
    for name, digest in env["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    witness = root / "witness.json"
    if hashlib.sha256(witness.read_bytes()).hexdigest() != result["witness_sha256"]:
        raise RuntimeError("external witness changed")
    rows, size = env["config"]["rows"], env["config"]["payload_bytes"]
    for name in (
        "healthy-scan",
        "tail-trailer-scan",
        "missing-tail-scan",
        "intact-backup",
    ):
        proof = aof.scan_proof(
            root / name / "state/aof-export.tsv", rows, size, witness
        )
        if name == "missing-tail-scan":
            expected = result["observations"]["missing-tail"]["fault"][
                "deleted_acknowledged_records"
            ]
            if proof["missing_acknowledged"] != expected or proof["wrong_bodies"]:
                raise RuntimeError("controlled tail-loss observation differs")
        else:
            aof_segments.require_complete(proof)
    if (
        fingerprint(sorted((root / "seed/state").glob("*.aof")))
        != result["observations"]["inventory"]["files"]
    ):
        raise RuntimeError("untouched native source changed")
    images, jobs = set(), {}
    for name, metrics in result["native_jobs"].items():
        stage = root / name
        info = load(stage / "after-inspection.json")
        if (
            info["State"]["Running"]
            or info["State"]["ExitCode"] != metrics["exit_code"]
        ):
            raise RuntimeError("native exit proof differs: " + name)
        config = info["HostConfig"]
        if config["NanoCpus"] != 10**9 or any(
            config[k] != 4 * 1024**3 for k in ("Memory", "MemorySwap")
        ):
            raise RuntimeError("native budget differs")
        images.add(info["Image"])
        timing = {}
        for line in (stage / "native.log").read_text().splitlines():
            if line.startswith("TIMING\t"):
                _, operation, value = line.split("\t", 2)
                timing[operation] = json.loads(value)
        compression = metrics["compressibility"]
        files = compression["files"]
        source_bytes = sum(item["bytes"] for item in files)
        archive_bytes = sum(item["gzip_level_1_bytes"] for item in files)
        jobs[name] = {
            "exit_code": metrics["exit_code"],
            "command": metrics["command"],
            "wall_ns": metrics["wall_ns"],
            "container_lifetime_seconds": metrics["container_lifetime_seconds"],
            "process_cpu_core_seconds": metrics["process_cpu_core_seconds"],
            "native_process_cpu_percent_one_core_100": metrics[
                "native_process_cpu_percent_one_core_100"
            ],
            "process_peak_rss_bytes": metrics["process_peak_rss_bytes"],
            "native_cgroup": metrics["cgroup"],
            "block_io": metrics["block_io"],
            "sampled_resources": metrics["sampled_resources"],
            "native_timings": timing,
            "compressibility": {
                "codec": "gzip",
                "level": 1,
                "source_bytes": source_bytes,
                "archive_bytes": archive_bytes,
                "ratio": source_bytes / archive_bytes,
                "allocated_bytes": sum(item["allocated_bytes"] for item in files),
                **{
                    key: compression[key]
                    for key in ("wall_ns", "cpu_ns", "zlib", "scope")
                },
            },
            "native_file_count": len(metrics["native_files"]),
            "native_file_bytes": sum(
                v["bytes"] for v in metrics["native_files"].values()
            ),
            "native_result_sha256": hashlib.sha256(
                (stage / "native-result.json").read_bytes()
            ).hexdigest(),
        }
    if len(images) != 1:
        raise RuntimeError("native jobs used different images")
    observations = copy.deepcopy(result["observations"])
    original = observations["inventory"]["files"]
    for name in ("tail-trailer", "sealed-crc", "missing-tail"):
        cell = observations[name]
        files = cell.pop("input_files")
        cell["input_manifest_sha256"] = hashlib.sha256(
            json.dumps(files, sort_keys=True).encode()
        ).hexdigest()
        cell["input_file_count"] = len(files)
        cell["changed_native_files"] = {
            key: value for key, value in files.items() if original.get(key) != value
        }
        cell["missing_native_files"] = sorted(original.keys() - files.keys())
    artifacts = bench.storage_sample(root)
    report = {
        "source_directory": str(root.relative_to(bench.ROOT)),
        "environment": env,
        "diagnostic_passed": True,
        "original_integrity_failures_still_open": True,
        "observations": observations,
        "jobs": jobs,
        "compression": result["compression"],
        "controller": result["controller"],
        "witness_sha256": result["witness_sha256"],
        "image_id": next(iter(images)),
        "retained_host_artifact_storage": {
            key: artifacts[key] for key in ("logical_bytes", "allocated_bytes")
        },
        "limits": [
            "fenced offline copies, no live availability or process crash qualification",
            "single-copy tail deletion causes expected irreversible local loss, not proof of recovery from device loss",
            "open validates the last segment; full scan and an external ACK/inventory oracle are necessary",
            "separate single-tail length/commit-marker integrity failures remain failed",
            "tiny operations may finish before sampling; native cumulative/high-water counters retained",
            "copy/export/host CLI and Docker VM costs excluded from native-child timings",
            "block counters are Linux VM cgroup observations; physical I/O latency/queue depth unavailable",
        ],
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    ingestion.save(bench.ROOT / "AOF_SEGMENT_RESULTS.json", report)
    print("Published native AOF segment diagnostics:", root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_directory", type=Path)
    publish(parser.parse_args().result_directory)
