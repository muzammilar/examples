"""Native rotation/reopen and controlled sealed-file loss, with an external oracle."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import time
import uuid

import aof
import experiment as bench
import ingestion
from journal_restore import archive_compressibility, fingerprint
from journal_tail import native_job, require_empty_project


def require_complete(proof):
    if proof["missing_acknowledged"] or proof["wrong_bodies"]:
        raise RuntimeError("independent witness rejects incomplete/corrupt AOF")


def run():
    rows = bench.integer("AOF_ROWS", 10000, 300, 1000000)
    size = bench.integer("AOF_BYTES", 1024, 64, 1024**2)
    limit = bench.integer("AOF_SEGMENT_LIMIT", 67108864, 4096, 256 * 1024**2)
    if rows * size > 512 * 1024**2 or limit * 3 > rows * size:
        raise ValueError(
            "require <=512 MiB payload and at least three segments; use make test-aof-segments"
        )
    require_empty_project()
    root = bench.RESULTS / "aof-segments" / uuid.uuid4().hex
    root.mkdir(parents=True)
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    tracked = [
        Path(__file__),
        *[
            bench.ROOT / name
            for name in (
                'projects/rayforce-aof/scripts/aof.py',
                'projects/rayforce-journal/scripts/journal_tail.py',
                'projects/rayforce-journal/scripts/journal_volume.py',
                'projects/rayforce-journal/scripts/journal_restore.py',
                'scripts/experiment.py',
                "analytics-workload/cpp/aof.cpp",
                "analytics-workload/python/measure_aof.py",
                "docker/Dockerfile",
            )
        ],
        *sorted([*bench.ROOT.glob("docker-compose*.yml"), *(bench.ROOT / "deploy/compose").glob("docker-compose*.yml")]),
    ]
    ingestion.save(
        root / "environment.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in tracked
            },
            "sources": json.loads((bench.ROOT / "config/sources.json").read_text()),
            "host": list(os.uname()),
            "cpu_model": bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if os.uname().sysname == "Darwin"
            else "Unresolved; supply native CPU inventory",
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "config": {
                "rows": rows,
                "payload_bytes": size,
                "segment_limit_bytes": limit,
                "explicit_commit_batch": 100,
            },
            "expected": {
                "healthy_and_backup": "every committed body/LSN survives reopen and scan",
                "known_tail_trailer": "open discards only the known uncommitted three-byte suffix",
                "sealed_crc_damage": "full scan rejects corrupt committed segment; open is not a full scan",
                "deleted_tail": "single-copy records are lost; witness must reject before further writes; intact backup restores all",
            },
            "scope": "Fenced offline logical file edits/deletion on independent copies, not process/power/device faults or live availability; this does not qualify the known failing single-tail integrity contract",
        },
    )
    jobs, observations = {}, {}

    def job(name, mode, source=None):
        stage = root / name
        state = stage / "state"
        if source is None:
            state.mkdir(parents=True)
        else:
            aof.clone(source, state)
        jobs[name] = native_job(stage, aof_mode=mode)
        return state

    try:
        seed = job("seed", "seed")
        if jobs["seed"]["exit_code"] or aof.native_status(root / "seed", "open") != [
            0,
            rows,
        ]:
            raise RuntimeError("native rotating seed failed")
        receipts = [
            int(line.split("\t")[1])
            for line in (root / "seed/native.log").read_text().splitlines()
            if line.startswith("ACK\t")
        ]
        expected = list(range(100, rows + 1, 100)) + ([rows] if rows % 100 else [])
        if receipts != expected:
            raise RuntimeError("committed range receipts differ")
        witness = root / "witness.json"
        ingestion.save(
            witness,
            {
                "acknowledged_prefix": rows,
                "bytes_per_payload": size,
                "committed_range_ends": receipts,
                "payload_sha256_by_lsn": [
                    hashlib.sha256(aof.payload(i, size)).hexdigest()
                    for i in range(rows)
                ],
            },
        )
        segments = sorted(seed.glob("*.aof"))
        if len(segments) < 3 or int(segments[0].stem) != 0:
            raise RuntimeError(
                "native rotation did not produce three segments from LSN zero"
            )
        original = fingerprint(segments)
        observations["inventory"] = {
            "segments": len(segments),
            "first_lsn": int(segments[0].stem),
            "tail_first_lsn": int(segments[-1].stem),
            "files": original,
        }
        for mode in ("open", "scan"):
            state = job("healthy-" + mode, mode, seed)
            if (
                jobs["healthy-" + mode]["exit_code"]
                or fingerprint(sorted(state.glob("*.aof"))) != original
            ):
                raise RuntimeError(
                    "healthy multi-segment operation failed/changed native files"
                )
            if mode == "open" and aof.native_status(state.parent, mode) != [0, rows]:
                raise RuntimeError("healthy next LSN differs")
            if mode == "scan":
                proof = aof.scan_proof(state / "aof-export.tsv", rows, size, witness)
                require_complete(proof)
                observations["healthy_scan"] = proof
        # Prepare isolated changes only after native seed has exited and its Linux
        # volume is exported. The untouched source is never edited.
        for fault in ("tail-trailer", "sealed-crc", "missing-tail"):
            damaged = root / (fault + "-input")
            aof.clone(seed, damaged)
            target = damaged / (
                segments[len(segments) // 2].name
                if fault == "sealed-crc"
                else segments[-1].name
            )
            if fault == "tail-trailer":
                with target.open("ab") as stream:
                    stream.write(b"\x01\x02\x03")
                    stream.flush()
                    os.fsync(stream.fileno())
                edit = {"file": target.name, "appended_hex": "010203"}
            elif fault == "sealed-crc":
                data = bytearray(target.read_bytes())
                length, _ = aof.HEADER.unpack_from(data)
                if length != size or data[8 : 8 + size] != aof.payload(
                    int(target.stem), size
                ):
                    raise RuntimeError("controlled sealed-record fixture differs")
                offset = 8 + size - 1
                old = data[offset]
                data[offset] ^= 1
                with target.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                edit = {
                    "file": target.name,
                    "offset": offset,
                    "old_byte": old,
                    "new_byte": data[offset],
                    "bits_changed": 1,
                }
            else:
                edit = {
                    "file": target.name,
                    "deleted_acknowledged_records": rows - int(target.stem),
                }
                target.unlink()
                descriptor = os.open(damaged, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            before = fingerprint(sorted(damaged.glob("*.aof")))
            cell = observations[fault] = {"fault": edit, "input_files": before}
            for mode in ("scan", "open"):
                state = job(fault + "-" + mode, mode, damaged)
                status = aof.native_status(state.parent, mode)
                code = jobs[fault + "-" + mode]["exit_code"]
                after = fingerprint(sorted(state.glob("*.aof")))
                observation = {
                    "native_status": status,
                    "exit_code": code,
                    "files_changed": after != before,
                }
                cell[mode] = observation
                if fault == "sealed-crc" and mode == "scan":
                    if code != 2 or status[0] != 10 or after != before:
                        raise RuntimeError(
                            "sealed committed CRC failure was not rejected without mutation"
                        )
                    observation["verdict"] = (
                        "Native full scan rejected corrupt committed data"
                    )
                    continue
                if code:
                    raise RuntimeError("unexpected native operation failure")
                if fault == "tail-trailer":
                    if mode == "open" and after != original:
                        raise RuntimeError(
                            "tail recovery changed more than the known uncommitted suffix"
                        )
                    if mode == "scan" and after != before:
                        raise RuntimeError("read-only scan changed the known trailer")
                elif after != before:
                    raise RuntimeError(
                        "native operation unexpectedly changed damaged files"
                    )
                expected_next = (
                    int(segments[-1].stem) if fault == "missing-tail" else rows
                )
                if mode == "open" and status != [0, expected_next]:
                    raise RuntimeError(
                        "native open next LSN differs from the observed file inventory"
                    )
                if mode == "scan":
                    proof = aof.scan_proof(
                        state / "aof-export.tsv", rows, size, witness
                    )
                    observation["proof"] = proof
                    if fault == "missing-tail":
                        if (
                            proof["missing_acknowledged"]
                            != edit["deleted_acknowledged_records"]
                            or proof["wrong_bodies"]
                        ):
                            raise RuntimeError(
                                "deleted-tail loss differs from independent ACK witness"
                            )
                        try:
                            require_complete(proof)
                        except RuntimeError:
                            observation["witness_rejected"] = True
                        else:
                            raise RuntimeError("witness accepted missing ACK bodies")
                    else:
                        require_complete(proof)
            if fingerprint(sorted(damaged.glob("*.aof"))) != before:
                raise RuntimeError(
                    "fault source changed during independent observations"
                )
        restored = job("intact-backup", "scan", seed)
        proof = aof.scan_proof(restored / "aof-export.tsv", rows, size, witness)
        require_complete(proof)
        if (
            jobs["intact-backup"]["exit_code"]
            or fingerprint(sorted(seed.glob("*.aof"))) != original
        ):
            raise RuntimeError("backup scan failed or original native source changed")
        observations["backup"] = proof
        require_empty_project()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result = {
            "diagnostic_passed": True,
            "original_integrity_failures_still_open": True,
            "observations": observations,
            "native_jobs": jobs,
            "compression": archive_compressibility(segments),
            "witness_sha256": hashlib.sha256(witness.read_bytes()).hexdigest(),
            "controller": {
                "wall_ns": time.monotonic_ns() - started,
                "cpu_ns": time.process_time_ns() - cpu,
                "peak_rss_bytes": usage.ru_maxrss
                * (1 if os.uname().sysname == "Darwin" else 1024),
            },
            "scope": "Observed expected loss after deleting an acknowledged single-copy tail; external witness rejects and untouched backup restores. Native open checks only the tail, not every sealed segment. No silent-loss integrity qualification or live crash claim.",
        }
        ingestion.save(root / "result.json", result)
        print("Native AOF segment diagnostic passed:", root, flush=True)
        return root
    except Exception as error:
        ingestion.save(root / "failure.json", {"passed": False, "error": repr(error)})
        raise


if __name__ == "__main__":
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()
