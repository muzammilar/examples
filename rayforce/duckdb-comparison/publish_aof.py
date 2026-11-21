"""Publish the explicit AOF fault model without hiding a silent-loss result."""

import argparse
import hashlib
import json
from pathlib import Path

from aof import faults, scan_proof
import experiment as bench
import ingestion
from journal_restore import fingerprint


def load(path):
    return json.loads(path.read_text())


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "aof").resolve()):
        raise ValueError("select an owned AOF result directory")
    result, environment = [
        load(root / name) for name in ("result.json", "environment.json")
    ]
    if not result["diagnostic_completed"]:
        raise RuntimeError("AOF diagnostic is incomplete")
    for name, digest in environment["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    if fingerprint([root / "witness.json"]) != result["witness_files"]:
        raise RuntimeError("external acknowledgement witness changed")
    if any(
        result[p][k]
        for p in ("baseline_proof", "backup_proof")
        for k in ("missing_acknowledged", "wrong_bodies", "duplicate_ids")
    ):
        raise RuntimeError("baseline or untouched backup did not verify")
    original = sorted((root / "seed/state").glob("*.aof"))
    if len(original) != 1 or fingerprint(original) != result["original_files"]:
        raise RuntimeError("original committed segment changed")
    expected_faults = faults(original[0].read_bytes())
    if result["cases"].keys() != expected_faults.keys():
        raise RuntimeError("fault matrix is incomplete")
    failed = False
    rows = environment["workload"]["rows"]
    size = environment["workload"]["bytes_per_payload"]
    for stage_name, proof_name in (
        ("baseline-scan", "baseline_proof"),
        ("backup", "backup_proof"),
    ):
        if (
            scan_proof(
                root / stage_name / "state/aof-export.tsv",
                rows,
                size,
                root / "witness.json",
            )
            != result[proof_name]
        ):
            raise RuntimeError(
                "healthy/backup export differs from the independent oracle"
            )
    for name, (changed, detail) in expected_faults.items():
        record = result["cases"][name]
        preserved = root / name / "damaged-source"
        if (
            record["fault"] != detail
            or fingerprint(sorted(preserved.glob("*.aof"))) != record["damaged_files"]
            or hashlib.sha256((preserved / original[0].name).read_bytes()).hexdigest()
            != hashlib.sha256(changed).hexdigest()
        ):
            raise RuntimeError("controlled fault or preserved evidence changed")
        for mode, observation in record["observations"].items():
            proof = observation.get("proof", {})
            if proof:
                stage_name = "scan-after-open" if mode == "open" else "scan"
                if (
                    scan_proof(
                        root / name / stage_name / "state/aof-export.tsv",
                        rows,
                        size,
                        root / "witness.json",
                    )
                    != proof
                ):
                    raise RuntimeError(
                        "recorded corrupted-copy export differs from the oracle"
                    )
            failed |= bool(
                proof.get("missing_acknowledged") or proof.get("wrong_bodies")
            )
    if result["integrity_passed"] != (not failed):
        raise RuntimeError("AOF integrity verdict disagrees with body evidence")
    images = set()
    for name, metrics in result["native_jobs"].items():
        stage = root / name
        info, fenced = [
            load(stage / filename)
            for filename in ("after-inspection.json", "volume-export-fenced.json")
        ]
        if (
            info["State"]["Running"]
            or fenced["State"]["Running"]
            or info["State"]["ExitCode"] != metrics["exit_code"]
        ):
            raise RuntimeError("native process/export is not fenced")
        limits = info["HostConfig"]
        if (
            limits["NanoCpus"] != 10**9
            or limits["Memory"] != 4 * 1024**3
            or limits["MemorySwap"] != 4 * 1024**3
        ):
            raise RuntimeError("effective native budget changed")
        images.add(info["Image"])
        if (
            fingerprint(sorted((stage / "state").glob("*.aof")))
            != metrics["native_files"]
        ):
            raise RuntimeError("preserved native job state changed")
        if not metrics.get("api_timings"):
            raise RuntimeError("selected run lacks native API operation timing")
        for timing in metrics["api_timings"].values():
            if (
                timing["count"] < 1
                or not 0
                <= timing["min_ns"]
                <= timing["p50_ns"]
                <= timing["p99_ns"]
                <= timing["max_ns"]
                or not timing["min_ns"] <= timing["mean_ns"] <= timing["max_ns"]
            ):
                raise RuntimeError("native API timing summary is inconsistent")
        if name == "seed" and (
            metrics["api_timings"]["buffered_append"]["count"] != rows
            or metrics["api_timings"]["commit_barrier"]["count"]
            != len(load(root / "witness.json")["committed_range_ends"])
        ):
            raise RuntimeError(
                "native API sample counts differ from witnessed operations"
            )
        seconds = bench.container_seconds(info)
        if seconds != metrics["container_lifetime_seconds"]:
            raise RuntimeError("I/O counter timing boundary differs")
        expected_io = bench.io_counters(metrics["cgroup"]["io.stat"], seconds)
        if (
            expected_io["totals"] != metrics["block_io"]["totals"]
            or expected_io["per_second"] != metrics["block_io"]["per_second"]
        ):
            raise RuntimeError("block bytes/IOPS arithmetic differs")
        metrics["settled_state"] = load(stage / "volume-export.json")
        initialization = load(stage / "volume-initialization.json")
        init_info = initialization["inspection"]
        init_limits = init_info["HostConfig"]
        if (
            init_info["State"]["Running"]
            or init_info["State"]["ExitCode"]
            or init_info["Image"] != info["Image"]
            or init_limits["NanoCpus"] != 10**9
            or init_limits["Memory"] != 512 * 1024**2
            or init_limits["MemorySwap"] != 512 * 1024**2
        ):
            raise RuntimeError(
                "volume initialization did not match the measured setup budget"
            )
        metrics["volume_initialization"] = {
            "resources": json.loads(initialization["log"]),
            "image": initialization["inspection"]["Image"],
            "limits": {
                k: initialization["inspection"]["HostConfig"][k]
                for k in ("NanoCpus", "Memory", "MemorySwap")
            },
        }
        samples = load(stage / "samples.json")["disks"]
        metrics["sampled_state_bytes_max_including_exports"] = max(
            [v["logical_bytes"] for v in samples if "logical_bytes" in v]
            + [metrics["settled_state"]["logical_bytes"]]
        )
        metrics["process_cpu_utilization_one_core_100"] = (
            100 * metrics["process_cpu_core_seconds"] / (metrics["wall_ns"] / 1e9)
        )
    if len(images) != 1:
        raise RuntimeError("native image changed across cases")
    image = json.loads(
        bench.capture(["docker", "image", "inspect", next(iter(images))], text=True)
    )[0]
    for kind, flags in (
        ("ps", ["-aq"]),
        ("network", ["ls", "-q"]),
        ("volume", ["ls", "-q"]),
    ):
        if bench.capture(
            [
                "docker",
                kind,
                *flags,
                "--filter",
                "label=com.docker.compose.project=rayforce-duckdb-journal",
            ],
            text=True,
        ).strip():
            raise RuntimeError("example-owned resources remain: " + kind)
    disk = bench.storage_sample(root)
    ingestion.save(
        bench.ROOT / "AOF_RESULTS.json",
        {
            "source_directory": str(root.relative_to(bench.ROOT)),
            "result": result,
            "environment": environment,
            "runtime_image": {k: image[k] for k in ("Id", "Architecture", "Os")},
            "retained_case_host_disk": {
                k: disk[k] for k in ("logical_bytes", "allocated_bytes")
            },
            "cleanup_verified": True,
            "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": "Strict no-silent-ACK-loss integrity evaluation under explicit offline file-edit faults, not ordinary process-crash or physical-power-loss qualification. Native success with lost committed records remains FAILED; backup recovery is independent.",
        },
    )
    print(
        "Published AOF integrity",
        "FAILED" if failed else "PASSED",
        "and independently verified backup",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    publish(parser.parse_args().case)
