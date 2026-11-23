"""Logical payload corruption: native framing checks versus a durable body oracle."""

import argparse
import csv
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import resource
import struct
import time
import uuid

import experiment as bench
import ingestion
from journal_restore import fingerprint
from journal_tail import (
    clone_native,
    native_job,
    repair_known_tail,
    require_empty_project,
)
from live_journal import reconcile


# Pinned serde.h v3 layout, used only to locate a controlled fault. Native CLI
# validation/replay remains authoritative; this is not a replacement decoder.
HEADER = struct.Struct("<IBBBBq")
INSERT = re.compile(
    rb"\(insert 'trades \(list \[(\d+)\] \[(\d+)\] \[(\d+)\] \[(\d+)\] \[(\d+)\] \[(\d+)\]\)\)"
)


def frames(data):
    if data[:8] != b"RAYJLG1\0" or len(data) < 16:
        raise ValueError("expected pinned generation preamble")
    offset, result = 16, []
    while offset < len(data):
        if offset + HEADER.size > len(data):
            raise ValueError("incomplete frame header")
        prefix, version, flags, endian, message, size = HEADER.unpack_from(data, offset)
        if (
            prefix != 0xCEFADEFA
            or version != 3
            or endian != 0
            or not 0 < size <= 256 * 1024**2
        ):
            raise ValueError("unsupported pinned frame")
        end = offset + HEADER.size + size
        if end > len(data):
            raise ValueError("incomplete frame payload")
        result.append(
            {
                "offset": offset,
                "payload_offset": offset + HEADER.size,
                "end": end,
                "flags": flags,
                "message": message,
            }
        )
        offset = end
    return result


def choose_acknowledged_insert(data, acknowledged):
    candidates = []
    all_frames = frames(data)
    for index, frame in enumerate(all_frames):
        payload = data[frame["payload_offset"] : frame["end"]]
        if (
            frame["flags"]
            or frame["message"] != 1
            or len(payload) < 10
            or payload[:2] != b"\xf3\x00"
        ):
            continue
        length = struct.unpack_from("<q", payload, 2)[0]
        if length != len(payload) - 10:
            continue
        match = INSERT.fullmatch(payload[10:])
        if match:
            row = list(map(int, match.groups()))
            if acknowledged.get(row[0]) == row:
                candidates.append(
                    dict(
                        frame,
                        index=index,
                        row=row,
                        price_digit_offset=frame["payload_offset"]
                        + 10
                        + match.end(6)
                        - 1,
                    )
                )
    if len(candidates) < 3:
        raise ValueError("need at least three acknowledged active-log inserts")
    selected = candidates[len(candidates) // 2]
    if selected["index"] in (0, len(all_frames) - 1):
        raise ValueError("fault must be interior, with frames on both sides")
    return dict(
        selected,
        total_frames=len(all_frames),
        acknowledged_insert_frames=len(candidates),
    )


def mutate(data, selected, mode):
    changed = bytearray(data)
    if mode == "invalid-type":
        offset, value = selected["payload_offset"], 127
    elif mode == "valid-literal":
        offset = selected["price_digit_offset"]
        if not ord("0") <= data[offset] <= ord("9"):
            raise ValueError("price fault does not point at an ASCII digit")
        value = data[offset] ^ 1  # 0↔1, 2↔3, …, 8↔9: valid syntax, one-bit change.
    else:
        raise ValueError("unknown payload fault")
    before = changed[offset]
    changed[offset] = value
    if frames(data) != frames(changed) or before == value:
        raise RuntimeError("fault changed framing or did not change payload")
    details = {
        "offset": offset,
        "old_byte": before,
        "new_byte": value,
        "bits_changed": (before ^ value).bit_count(),
    }
    if mode == "valid-literal":
        details["expected_mutated_row"] = list(selected["row"])
        details["expected_mutated_row"][-1] += value - before
    return bytes(changed), details


def compare_complete_exports(good, bad):
    mismatches, samples, rows = 0, [], 0
    with good.open() as correct, bad.open() as corrupt:
        expected, actual = csv.reader(correct), csv.reader(corrupt)
        if next(expected) != next(actual):
            raise RuntimeError("corruption changed export schema")
        for left, right in itertools.zip_longest(expected, actual):
            if left is None or right is None or left[0] != right[0]:
                raise RuntimeError("corrupted export changed cardinality/ID order")
            rows += 1
            if left != right:
                mismatches += 1
                if len(samples) < 10:
                    samples.append(
                        {
                            "expected": list(map(int, left)),
                            "actual": list(map(int, right)),
                        }
                    )
    return {
        "rows_compared": rows,
        "wrong_bodies": mismatches,
        "samples": samples,
        "missing_or_duplicate_ids": 0,
    }


def validated(root, expected_frames, expected_bytes):
    result = native_job(root, validate_only=True)
    numbers = [
        int(line)
        for line in (root / "native.log").read_text().splitlines()
        if re.fullmatch(r"\d+", line)
    ]
    if result["exit_code"] or numbers != [expected_frames, expected_bytes]:
        raise RuntimeError("native framing validation did not accept the intact frames")
    return result


def run(source):
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    source = source.resolve(strict=True)
    if not source.is_relative_to((bench.RESULTS / "journal-live").resolve()):
        raise ValueError("source must be an owned journal-live case")
    prior = json.loads((source / "result.json").read_text())
    if not prior["passed"] or not prior.get("finalized_after_cleanup"):
        raise RuntimeError("source has not completed correctness/teardown")
    require_empty_project()
    setup = json.loads((source / "setup.json").read_text())
    for name, digest in setup["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("independent input fixture differs")
    events = [
        json.loads(line) for line in (source / "history.jsonl").read_text().splitlines()
    ]
    intents = {e["id"]: e["row"] for e in events if e["kind"] == "intent"}
    acknowledged = {
        e["id"]: intents[e["id"]]
        for e in events
        if e["kind"] == "write" and e["status"] == "ok"
    }
    original = fingerprint(sorted((source / "state").glob("journal*")))
    data = (source / "state/journal.log").read_bytes()
    selected = choose_acknowledged_insert(data, acknowledged)
    root = bench.RESULTS / "journal-corruption" / uuid.uuid4().hex
    root.mkdir(parents=True)
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
            "fixture": setup["fixture"],
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    bench.ROOT / "projects/rayforce-journal/scripts/journal_tail.py",
                    bench.ROOT / "projects/rayforce-journal/scripts/journal_volume.py",
                    bench.ROOT / "projects/rayforce-journal/scripts/journal_restore.py",
                    bench.ROOT / "projects/rayforce-journal/scripts/live_journal.py",
                    bench.ROOT / "deploy/compose/docker-compose.journal-tail.yml",
                    bench.ROOT / "deploy/compose/docker-compose.journal-volume.yml",
                    bench.ROOT / "deploy/compose/docker-compose.journal.yml",
                    bench.ROOT / "docker/Dockerfile",
                ]
            },
            "contract": "Logical one-byte edits to a fenced copy, not physical bit rot/power loss. Invalid serialized type must fail startup. A native successful export with wrong acknowledged body is a FAILED integrity result. Never truncate an interior fault. Restore from an untouched backup and verify every body.",
        },
    )
    fault_records, jobs, native_files = {}, {}, {}
    for mode in ("invalid-type", "valid-literal"):
        changed, fault = mutate(data, selected, mode)
        stage = root / mode
        clone_native(source / "state", stage / "state")
        with (stage / "state/journal.log").open("wb") as stream:
            stream.write(changed)
            stream.flush()
            os.fsync(stream.fileno())
        expected = fingerprint(sorted((stage / "state").glob("journal*")))
        native_files[mode] = expected
        fault_records[mode] = dict(
            fault,
            selected_frame=selected,
            log_bytes=len(changed),
            sha256=hashlib.sha256(changed).hexdigest(),
        )
        validation = root / (mode + "-validation")
        clone_native(stage / "state", validation / "state")
        jobs[mode + "-validation"] = validated(
            validation, selected["total_frames"], len(data)
        )
        if fingerprint(sorted((validation / "state").glob("journal*"))) != expected:
            raise RuntimeError("native validation altered damaged data")
        jobs[mode] = native_job(stage)
        if fingerprint(sorted((stage / "state").glob("journal*"))) != expected:
            raise RuntimeError("native startup altered corrupt evidence")
        # Framing acceptance is never treated as permission to cut this log.
        try:
            repair_known_tail(
                stage / "state",
                root / (mode + "-unsafe-repair"),
                len(data),
                original["journal.log"],
            )
        except ValueError:
            fault_records[mode]["tail_repair_rejected"] = True
        else:
            raise RuntimeError("tail repair incorrectly accepted interior corruption")
    raw = (root / "invalid-type/native.log").read_text()
    if (
        jobs["invalid-type"]["exit_code"] != 2
        or "deserialization failed" not in raw
        or "Do NOT truncate" not in raw
        or "BOOT_REACHED" in raw
        or "listening on" in raw
    ):
        raise RuntimeError("invalid payload did not fail closed as content corruption")
    backup = root / "backup"
    clone_native(source / "state", backup / "state")
    jobs["backup"] = native_job(backup)
    if (
        jobs["backup"]["exit_code"]
        or "(0 eval errors)" not in (backup / "native.log").read_text()
    ):
        raise RuntimeError("untouched backup did not restore cleanly")
    proof = reconcile(
        bench.RESULTS / "data/trades.csv", backup / "state/restored.csv", events
    )
    dimension = bench.RESULTS / "data/customers.csv"
    customers = ingestion.verify_export(
        backup / "state/restored-customers.csv",
        {
            "rows": 10000,
            "bytes": dimension.stat().st_size,
            "sha256": setup["fixture"]["sha256"]["customers.csv"],
        },
    )
    semantic = jobs["valid-literal"]
    mismatch, oracle_error = None, None
    if semantic["exit_code"] == 0:
        try:
            reconcile(
                bench.RESULTS / "data/trades.csv",
                root / "valid-literal/state/restored.csv",
                events,
            )
        except RuntimeError as error:
            oracle_error = str(error)
        mismatch = compare_complete_exports(
            backup / "state/restored.csv", root / "valid-literal/state/restored.csv"
        )
        if (
            mismatch["wrong_bodies"] != 1
            or mismatch["samples"][0]["expected"] != selected["row"]
            or mismatch["samples"][0]["actual"]
            != fault_records["valid-literal"]["expected_mutated_row"]
            or not oracle_error
        ):
            raise RuntimeError(
                "logical corruption produced an unexpected integrity outcome"
            )
        verdict = "FAILED: validly encoded one-bit payload corruption replayed without native rejection and changed an acknowledged body"
    elif (
        semantic["exit_code"] == 2
        and "BOOT_REACHED" not in (root / "valid-literal/native.log").read_text()
    ):
        verdict = (
            "PASSED: native startup rejected semantic payload corruption before boot"
        )
    else:
        raise RuntimeError("semantic corruption job has an unexplained outcome")
    if fingerprint(sorted((source / "state").glob("journal*"))) != original:
        raise RuntimeError("untouched source changed during diagnostics")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "diagnostic_completed": True,
        "integrity_verdict": verdict,
        "source": str(source.relative_to(bench.ROOT)),
        "original_files": original,
        "corrupt_native_files": native_files,
        "faults": fault_records,
        "native_jobs": jobs,
        "semantic_mismatches": mismatch,
        "independent_oracle_error": oracle_error,
        "backup_recovery_proof": proof,
        "customers": customers,
        "source_and_corrupt_evidence_preserved": True,
        "host_controller": {
            "wall_ns": time.monotonic_ns() - started,
            "cpu_ns": time.process_time_ns() - cpu,
            "peak_rss_bytes": usage.ru_maxrss
            * (1 if os.uname().sysname == "Darwin" else 1024),
        },
        "scope": "Logical file-payload corruption beyond the healthy/process-crash contract. Native framing acceptance is not content integrity. Integrity failure remains FAILED despite successful backup recovery; no universal production/physical-media conclusion.",
    }
    ingestion.save(root / "result.json", result)
    print(verdict, root, flush=True)
    return verdict.startswith("PASSED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_case", type=Path)
    args = parser.parse_args()
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise SystemExit(0 if run(args.source_case) else 1)
