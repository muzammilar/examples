"""Qualify the separate native AOF contract using fenced, checksummed copies."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import struct
import time
import uuid
import zlib

import experiment as bench
import ingestion
from journal_restore import fingerprint
from journal_tail import native_job, require_empty_project

HEADER = struct.Struct("<II")
COMMIT_LENGTH, COMMIT_MAGIC = 0xFFFFFFFF, 0xB0AD63C9


def payload(identity, size):
    prefix = str(identity).encode() + b":"
    return prefix + bytes(
        97 + (identity * 17 + offset) % 26 for offset in range(len(prefix), size)
    )


def layout(data):
    """Locate controlled faults in the pinned fixture, not a replacement AOF reader."""
    records, commits, offset = [], [], 0
    while offset < len(data):
        if offset + HEADER.size > len(data):
            raise ValueError("fixture has an incomplete header")
        length, crc = HEADER.unpack_from(data, offset)
        if length == COMMIT_LENGTH:
            if crc != COMMIT_MAGIC:
                raise ValueError("fixture commit marker differs")
            commits.append({"offset": offset, "committed_records": len(records)})
            offset += HEADER.size
            continue
        end = offset + HEADER.size + length
        if not 64 <= length <= 1024**2 or end > len(data):
            raise ValueError("fixture record size differs")
        body = data[offset + HEADER.size : end]
        if zlib.crc32(data[offset : offset + 4] + body) != crc:
            raise ValueError("fixture CRC differs from pinned length+payload format")
        records.append(
            {"offset": offset, "payload_offset": offset + HEADER.size, "end": end}
        )
        offset = end
    if (
        len(records) < 300
        or len(commits) < 3
        or commits[-1]["committed_records"] != len(records)
    ):
        raise ValueError(
            "fixture needs at least three committed groups and no uncommitted suffix"
        )
    return records, commits


def faults(data):
    records, commits = layout(data)
    record = records[len(records) // 2]
    selected = {
        "payload": record["end"] - 1,
        "length": record["offset"],
        "middle-commit": commits[len(commits) // 2]["offset"] + 4,
        "last-commit": commits[-1]["offset"] + 4,
    }
    result = {}
    for name, offset in selected.items():
        changed = bytearray(data)
        changed[offset] ^= 1
        result[name] = (
            bytes(changed),
            {
                "offset": offset,
                "old_byte": data[offset],
                "new_byte": changed[offset],
                "bits_changed": 1,
            },
        )
    result["uncommitted-trailer"] = (
        data + b"\x01\x02\x03",
        {"appended_hex": "010203", "bytes_added": 3},
    )
    return result


def clone(source, destination):
    destination.mkdir(parents=True)
    files = sorted(source.glob("*.aof"))
    if not files:
        raise ValueError("refusing to use an empty native source")
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError("native source is not a regular segment")
        shutil.copyfile(path, destination / path.name)


def scan_proof(path, rows, size, witness):
    expected = json.loads(witness.read_text())
    if (
        expected["acknowledged_prefix"] != rows
        or expected["bytes_per_payload"] != size
        or len(expected["payload_sha256_by_lsn"]) != rows
    ):
        raise RuntimeError("persisted witness differs from the declared fixture")
    seen, wrong = set(), []
    with path.open("rb") as stream:
        for line in stream:
            raw_id, body = line.removesuffix(b"\n").split(b"\t", 1)
            identity = int(raw_id)
            if identity in seen or not 0 <= identity < rows:
                raise RuntimeError("unexpected/duplicate native LSN")
            seen.add(identity)
            if (
                len(body) != size
                or hashlib.sha256(body).hexdigest()
                != expected["payload_sha256_by_lsn"][identity]
            ):
                wrong.append(identity)
    missing = sorted(set(range(rows)) - seen)
    return {
        "acknowledged": rows,
        "bodies_verified": len(seen) - len(wrong),
        "missing_acknowledged": len(missing),
        "missing_ids": missing,
        "wrong_bodies": len(wrong),
        "wrong_ids": wrong,
        "duplicate_ids": 0,
    }


def native_status(root, mode):
    matches = [
        line.split("\t")
        for line in (root / "native.log").read_text().splitlines()
        if line.startswith(mode.upper() + "\t")
    ]
    if len(matches) != 1:
        raise RuntimeError("missing/ambiguous native application result")
    return list(map(int, matches[0][1:]))


def run():
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    rows = bench.integer("AOF_ROWS", 10000, 300, 1000000)
    size = bench.integer("AOF_BYTES", 1024, 64, 1024**2)
    if rows * size > 512 * 1024**2:
        raise ValueError("fixture exceeds the 512 MiB bound")
    # First qualify the tail segment. Multi-segment behavior is a separate case.
    if (
        int(os.environ.get("AOF_SEGMENT_LIMIT", 67108864))
        <= rows * (size + 8) + rows * 8
    ):
        raise ValueError(
            "this profile requires one segment; increase AOF_SEGMENT_LIMIT"
        )
    require_empty_project()
    root = bench.RESULTS / "aof" / uuid.uuid4().hex
    root.mkdir(parents=True)
    tracked = [
        Path(__file__),
        bench.ROOT / "journal_tail.py",
        bench.ROOT / "journal_volume.py",
        bench.ROOT / "journal_restore.py",
        bench.ROOT / "experiment.py",
        bench.ROOT / "analytics-workload/cpp/aof.cpp",
        bench.ROOT / "analytics-workload/python/measure_aof.py",
        bench.ROOT / "analytics-workload/CMakeLists.txt",
        bench.ROOT / "docker/Dockerfile",
        *sorted(bench.ROOT.glob("docker-compose*.yml")),
    ]
    ingestion.save(
        root / "environment.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "cpu_model": bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if os.uname().sysname == "Darwin"
            else "Unresolved; supply native CPU inventory",
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "sources": json.loads((bench.ROOT / "sources.json").read_text()),
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in tracked
            },
            "contract": "Opaque AOF API, not the -L request journal or automatic database persistence. Explicit commit before ACK; committed payload corruption must reject rather than silently lose acknowledged records. Fenced offline copies, not physical bit rot or live availability. Single tail segment; application takes an exclusive writer lock.",
            "workload": {
                "rows": rows,
                "bytes_per_payload": size,
                "payload_bytes": rows * size,
                "commit_batch": 100,
                "max_fixture_bytes": 512 * 1024**2,
                "distribution": "Deterministic repetitive ASCII by LSN; deliberately compressible synthetic data, not a production corpus.",
            },
        },
    )
    seed = root / "seed"
    (seed / "state").mkdir(parents=True)
    jobs = {"seed": native_job(seed, aof_mode="seed")}
    if jobs["seed"]["exit_code"] or native_status(seed, "open") != [0, rows]:
        raise RuntimeError("native seed failed")
    receipts = [
        int(line.split("\t")[1])
        for line in (seed / "native.log").read_text().splitlines()
        if line.startswith("ACK\t")
    ]
    if receipts != list(range(100, rows + 1, 100)) + ([rows] if rows % 100 else []):
        raise RuntimeError("not every committed range has an acknowledgement")
    ingestion.save(
        root / "witness.json",
        {
            "acknowledged_prefix": rows,
            "bytes_per_payload": size,
            "committed_range_ends": receipts,
            "payload_sha256_by_lsn": [
                hashlib.sha256(payload(i, size)).hexdigest() for i in range(rows)
            ],
            "scope": "External fsynced witness persisted after native commit receipts and fencing, before any offline fault. Not a live per-request crash witness.",
        },
    )
    witness = root / "witness.json"
    witness_files = fingerprint([witness])
    files = sorted((seed / "state").glob("*.aof"))
    if len(files) != 1:
        raise RuntimeError("single-tail profile unexpectedly rotated")
    original = fingerprint(files)
    source = files[0]
    data = source.read_bytes()
    baseline_open = root / "baseline-open"
    clone(seed / "state", baseline_open / "state")
    jobs["baseline-open"] = native_job(baseline_open, aof_mode="open")
    if (
        jobs["baseline-open"]["exit_code"]
        or native_status(baseline_open, "open") != [0, rows]
        or fingerprint(sorted((baseline_open / "state").glob("*.aof"))) != original
    ):
        raise RuntimeError("healthy native reopen failed or changed committed data")
    baseline = root / "baseline-scan"
    clone(seed / "state", baseline / "state")
    jobs["baseline-scan"] = native_job(baseline, aof_mode="scan")
    baseline_proof = scan_proof(baseline / "state/aof-export.tsv", rows, size, witness)
    if (
        jobs["baseline-scan"]["exit_code"]
        or native_status(baseline, "scan") != [0, rows, rows]
        or baseline_proof["missing_acknowledged"]
        or baseline_proof["wrong_bodies"]
    ):
        raise RuntimeError("healthy full-body baseline failed; do not inject faults")
    cases, integrity_failed = {}, False
    for name, (changed, fault) in faults(data).items():
        preserved = root / name / "damaged-source"
        clone(seed / "state", preserved)
        with (preserved / source.name).open("wb") as stream:
            stream.write(changed)
            stream.flush()
            os.fsync(stream.fileno())
        damaged = fingerprint(sorted(preserved.glob("*.aof")))
        observations = {}
        for mode in ("open", "scan"):
            stage = root / name / mode
            clone(preserved, stage / "state")
            jobs[name + "/" + mode] = native_job(stage, aof_mode=mode)
            status = native_status(stage, mode)
            after = fingerprint(sorted((stage / "state").glob("*.aof")))
            record = {
                "native_status": status,
                "exit_code": jobs[name + "/" + mode]["exit_code"],
                "files_changed": after != damaged,
            }
            if mode == "scan" and after != damaged:
                raise RuntimeError("read-only scan changed corrupt evidence")
            if status[0] == 0:
                if record["exit_code"]:
                    raise RuntimeError("native status/exit disagree")
                if mode == "open":
                    export = root / name / "scan-after-open"
                    clone(stage / "state", export / "state")
                    jobs[name + "/scan-after-open"] = native_job(
                        export, aof_mode="scan"
                    )
                    if (
                        jobs[name + "/scan-after-open"]["exit_code"]
                        or native_status(export, "scan")[0]
                    ):
                        raise RuntimeError("opened copy could not be scanned")
                else:
                    export = stage
                record["proof"] = scan_proof(
                    export / "state/aof-export.tsv", rows, size, witness
                )
                if (
                    record["proof"]["missing_acknowledged"]
                    or record["proof"]["wrong_bodies"]
                ):
                    integrity_failed = True
                    record["verdict"] = (
                        "FAILED: native success with acknowledged loss/corruption"
                    )
                else:
                    record["verdict"] = "PASSED: all acknowledged bodies retained"
                if (
                    name == "uncommitted-trailer"
                    and mode == "open"
                    and after != original
                ):
                    raise RuntimeError(
                        "trailer recovery did not restore the exact original segment"
                    )
            elif status[0] == 10 and record["exit_code"] == 2 and after == damaged:
                record["verdict"] = "PASSED: RAY_ERR_CORRUPT, evidence unchanged"
            else:
                raise RuntimeError("unexpected native error or destructive rejection")
            observations[mode] = record
        if fingerprint(sorted(preserved.glob("*.aof"))) != damaged:
            raise RuntimeError("preserved corrupt source changed")
        cases[name] = {
            "fault": fault,
            "damaged_files": damaged,
            "observations": observations,
        }
    backup = root / "backup"
    clone(seed / "state", backup / "state")
    jobs["backup"] = native_job(backup, aof_mode="scan")
    proof = scan_proof(backup / "state/aof-export.tsv", rows, size, witness)
    if (
        jobs["backup"]["exit_code"]
        or proof["missing_acknowledged"]
        or proof["wrong_bodies"]
    ):
        raise RuntimeError("pristine backup did not restore every body")
    if fingerprint(sorted((seed / "state").glob("*.aof"))) != original:
        raise RuntimeError("original native source changed")
    if fingerprint([witness]) != witness_files:
        raise RuntimeError("external witness changed during fault diagnostics")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    for name, metrics in jobs.items():
        native_log = root / name / "native.log"
        metrics["api_timings"] = {
            line.split("\t", 2)[1]: json.loads(line.split("\t", 2)[2])
            for line in native_log.read_text().splitlines()
            if line.startswith("TIMING\t")
        }
    result = {
        "diagnostic_completed": True,
        "integrity_passed": not integrity_failed,
        "cases": cases,
        "native_jobs": jobs,
        "original_files": original,
        "baseline_proof": baseline_proof,
        "witness_files": witness_files,
        "backup_proof": proof,
        "host_controller": {
            "wall_ns": time.monotonic_ns() - started,
            "cpu_ns": time.process_time_ns() - cpu,
            "peak_rss_bytes": usage.ru_maxrss
            * (1 if os.uname().sysname == "Darwin" else 1024),
        },
        "scope": "Single-segment, fenced offline AOF API corruption diagnostic. A successful backup does not erase native silent-loss findings. No live availability, capacity, physical bit rot, request-journal or database transaction guarantee.",
    }
    ingestion.save(root / "result.json", result)
    print(
        "Native AOF integrity",
        "PASSED" if not integrity_failed else "FAILED",
        root,
        flush=True,
    )
    return not integrity_failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit(0 if run() else 1)
