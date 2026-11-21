"""Middle-frame damage: reject startup, preserve evidence, test unsafe truncation."""

import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import time
import uuid

import experiment as bench
import ingestion
from journal_corruption import INSERT, choose_acknowledged_insert, frames
from journal_restore import fingerprint
from journal_tail import (
    clone_native,
    native_job,
    repair_known_tail,
    require_empty_project,
)
from live_journal import reconcile


def damage_header(data, selected, mode):
    offset = selected["offset"] + {"magic": 0, "version": 4}[mode]
    changed = bytearray(data)
    changed[offset] ^= 1 if mode == "magic" else 4
    return bytes(changed), {
        "offset": offset,
        "old_byte": data[offset],
        "new_byte": changed[offset],
    }


def suffix_acknowledgements(data, offset, acknowledged):
    lost = set()
    for frame in frames(data):
        if frame["offset"] < offset or frame["flags"] or frame["message"] != 1:
            continue
        payload = data[frame["payload_offset"] : frame["end"]]
        match = INSERT.fullmatch(payload[10:]) if payload[:2] == b"\xf3\0" else None
        if match:
            row = list(map(int, match.groups()))
            if acknowledged.get(row[0]) == row:
                lost.add(row[0])
    if not lost:
        raise ValueError("selected cut does not remove witnessed acknowledgements")
    return lost


def compare_prefix_restore(good, truncated, expected_missing):
    """Stream every body, allowing only the explicitly predicted missing ACK IDs."""
    missing, compared = set(), 0
    with good.open() as correct, truncated.open() as cut:
        expected, actual = csv.reader(correct), csv.reader(cut)
        if next(expected) != next(actual):
            raise RuntimeError("prefix restore changed schema")
        current = next(actual, None)
        for row in expected:
            identity = int(row[0])
            if current is not None and current[0] == row[0]:
                if current != row:
                    raise RuntimeError("prefix restore changed a surviving body")
                compared += 1
                current = next(actual, None)
            elif identity in expected_missing:
                missing.add(identity)
            else:
                raise RuntimeError("unexpected missing/duplicate/out-of-order body")
        if current is not None or missing != expected_missing:
            raise RuntimeError("prefix loss differs from the witnessed suffix")
    return {
        "surviving_bodies_verified": compared,
        "missing_acknowledged_ids": sorted(missing),
        "acknowledged_loss": len(missing),
        "wrong_or_duplicate_bodies": 0,
    }


def run(source):
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    source = source.resolve(strict=True)
    if not source.is_relative_to((bench.RESULTS / "journal-live").resolve()):
        raise ValueError("select an owned live-journal source")
    prior = json.loads((source / "result.json").read_text())
    if not prior["passed"] or not prior.get("finalized_after_cleanup"):
        raise ValueError("source correctness/cleanup did not pass")
    require_empty_project()
    setup = json.loads((source / "setup.json").read_text())
    for name, digest in setup["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("independent input fixture changed")
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
    lost = suffix_acknowledgements(data, selected["offset"], acknowledged)
    root = bench.RESULTS / "journal-header" / uuid.uuid4().hex
    root.mkdir(parents=True)
    tracked = [
        Path(__file__),
        bench.ROOT / "journal_corruption.py",
        bench.ROOT / "journal_tail.py",
        bench.ROOT / "journal_restore.py",
        bench.ROOT / "journal_volume.py",
        bench.ROOT / "live_journal.py",
        *sorted(bench.ROOT.glob("docker-compose.journal*.yml")),
        bench.ROOT / "docker/Dockerfile",
    ]
    ingestion.save(
        root / "environment.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "source_setup": setup,
            "witness_sha256": hashlib.sha256(
                (source / "history.jsonl").read_bytes()
            ).hexdigest(),
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in tracked
            },
            "contract": "Offline logical edits to fenced copies, not physical media faults. Native startup must reject both damaged headers. Guarded tail repair must reject. A deliberately unsafe cut is a separate destructive counterexample, never a supported repair. Untouched backup must recover every witnessed body.",
        },
    )
    jobs, faults = {}, {}
    for mode in ("magic", "version"):
        stage = root / mode
        clone_native(source / "state", stage / "state")
        changed, details = damage_header(data, selected, mode)
        with (stage / "state/journal.log").open("wb") as stream:
            stream.write(changed)
            stream.flush()
            os.fsync(stream.fileno())
        damaged = fingerprint(sorted((stage / "state").glob("journal*")))
        jobs[mode] = native_job(stage)
        raw = (stage / "native.log").read_text()
        if (
            jobs[mode]["exit_code"] != 2
            or "ERROR badtail" not in raw
            or "BOOT_REACHED" in raw
            or "listening on" in raw
            or f"truncate the file at offset {selected['offset']}" not in raw
        ):
            raise RuntimeError(
                "middle-header failure/hint differs from pinned behavior"
            )
        validation = root / (mode + "-validation")
        clone_native(stage / "state", validation / "state")
        jobs[mode + "-validation"] = native_job(validation, validate_only=True)
        numbers = [
            int(line)
            for line in (validation / "native.log").read_text().splitlines()
            if re.fullmatch(r"\d+", line)
        ]
        if jobs[mode + "-validation"]["exit_code"] or numbers != [
            selected["index"],
            selected["offset"],
        ]:
            raise RuntimeError(
                "native prefix validation differs from the known fault offset"
            )
        if (
            fingerprint(sorted((stage / "state").glob("journal*"))) != damaged
            or fingerprint(sorted((validation / "state").glob("journal*"))) != damaged
        ):
            raise RuntimeError("failed replay/validation changed corrupt evidence")
        try:
            repair_known_tail(
                stage / "state",
                root / (mode + "-rejected-repair"),
                numbers[1],
                original["journal.log"],
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("guard accepted interior damage as a synthetic trailer")
        faults[mode] = dict(
            details,
            native_files=damaged,
            validated_prefix_frames=numbers[0],
            validated_prefix_bytes=numbers[1],
            guard_rejected=True,
        )
    backup = root / "backup"
    clone_native(source / "state", backup / "state")
    jobs["backup"] = native_job(backup)
    if (
        jobs["backup"]["exit_code"]
        or "(0 eval errors)" not in (backup / "native.log").read_text()
    ):
        raise RuntimeError("pristine backup failed native replay")
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
    # Deliberately unsafe counterexample on ANOTHER COPY: preserve damaged files,
    # original source and verified backup. Never expose this as a repair command.
    unsafe = root / "unsafe-prefix-counterexample"
    clone_native(root / "magic/state", unsafe / "state")
    with (unsafe / "state/journal.log").open("r+b") as stream:
        stream.truncate(selected["offset"])
        stream.flush()
        os.fsync(stream.fileno())
    jobs["unsafe-prefix"] = native_job(unsafe)
    if (
        jobs["unsafe-prefix"]["exit_code"]
        or "(0 eval errors)" not in (unsafe / "native.log").read_text()
    ):
        raise RuntimeError(
            "unsafe prefix did not reproduce clean but incomplete startup"
        )
    try:
        reconcile(
            bench.RESULTS / "data/trades.csv", unsafe / "state/restored.csv", events
        )
    except RuntimeError as error:
        oracle_error = str(error)
    else:
        raise RuntimeError("full witness failed to reject acknowledged loss")
    loss = compare_prefix_restore(
        backup / "state/restored.csv", unsafe / "state/restored.csv", lost
    )
    ingestion.verify_export(
        unsafe / "state/restored-customers.csv",
        {
            "rows": 10000,
            "bytes": dimension.stat().st_size,
            "sha256": setup["fixture"]["sha256"]["customers.csv"],
        },
    )
    if fingerprint(sorted((source / "state").glob("journal*"))) != original:
        raise RuntimeError("original evidence changed")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "passed": True,
        "source": str(source.relative_to(bench.ROOT)),
        "original_files": original,
        "selected_frame": selected,
        "faults": faults,
        "native_jobs": jobs,
        "backup_proof": proof,
        "customers": customers,
        "unsafe_prefix_loss": loss,
        "unsafe_prefix_verdict": "FAILED durability if the native truncation hint is followed for this interior fault; not an approved repair",
        "independent_oracle_error": oracle_error,
        "host_controller": {
            "wall_ns": time.monotonic_ns() - started,
            "cpu_ns": time.process_time_ns() - cpu,
            "peak_rss_bytes": usage.ru_maxrss
            * (1 if os.uname().sysname == "Darwin" else 1024),
        },
        "scope": "Diagnostic passed: fail-closed middle-header startup, guard rejection, exact backup recovery, and quantified data loss on a separate unsafe-prefix copy. Unsafe recovery remains FAILED. Not physical bit rot or normal crash loss.",
    }
    ingestion.save(root / "result.json", result)
    print(
        "Middle-header diagnostic passed; unsafe prefix LOST",
        len(lost),
        "ACKs:",
        root,
        flush=True,
    )


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "journal-header").resolve()):
        raise ValueError("select an owned journal-header case")
    result = json.loads((root / "result.json").read_text())
    environment = json.loads((root / "environment.json").read_text())
    if not result["passed"] or result["unsafe_prefix_loss"]["acknowledged_loss"] < 1:
        raise RuntimeError("incomplete header diagnostic")
    for name, digest in environment["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    source = bench.ROOT / result["source"]
    if (
        hashlib.sha256((source / "history.jsonl").read_bytes()).hexdigest()
        != environment["witness_sha256"]
        or fingerprint(sorted((source / "state").glob("journal*")))
        != result["original_files"]
    ):
        raise RuntimeError("original witness/native state changed")
    for name, digest in environment["source_setup"]["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("independent fixture changed")
    images = set()
    for name, metrics in result["native_jobs"].items():
        stage = root / (
            "unsafe-prefix-counterexample" if name == "unsafe-prefix" else name
        )
        info = json.loads((stage / "after-inspection.json").read_text())
        exported = json.loads((stage / "volume-export-fenced.json").read_text())
        if (
            info["State"]["Running"]
            or exported["State"]["Running"]
            or info["State"]["ExitCode"] != metrics["exit_code"]
        ):
            raise RuntimeError("native job/export is not fenced")
        if (
            fingerprint(sorted((stage / "state").glob("journal*")))
            != metrics["native_files"]
        ):
            raise RuntimeError("preserved native files changed")
        config = info["HostConfig"]
        if (
            config["NanoCpus"] != 10**9
            or config["Memory"] != 4 * 1024**3
            or config["MemorySwap"] != 4 * 1024**3
        ):
            raise RuntimeError("native budget differs")
        images.add(info["Image"])
        metrics["native_process_cpu_percent_one_core_100"] = (
            100 * metrics["process_cpu_core_seconds"] / (metrics["wall_ns"] / 1e9)
        )
        metrics["container_lifetime_seconds"] = bench.container_seconds(info)
        metrics["block_io"] = bench.io_counters(
            metrics["cgroup"]["io.stat"], metrics["container_lifetime_seconds"]
        )
        metrics["block_io"]["rate_scope"] = (
            "Whole-container counters over container lifetime, not native-child wall."
        )
        metrics["volume_export"] = json.loads(
            (stage / "volume-export.json").read_text()
        )
        samples = json.loads((stage / "samples.json").read_text())["disks"]
        metrics["sampled_state_bytes_max_including_exports"] = max(
            [s["logical_bytes"] for s in samples if "logical_bytes" in s]
            + [metrics["volume_export"]["logical_bytes"]]
        )
    if len(images) != 1:
        raise RuntimeError("native image changed")
    for resource_name, flags in (
        ("ps", ["-aq"]),
        ("network", ["ls", "-q"]),
        ("volume", ["ls", "-q"]),
    ):
        if bench.capture(
            [
                "docker",
                resource_name,
                *flags,
                "--filter",
                "label=com.docker.compose.project=rayforce-duckdb-journal",
            ],
            text=True,
        ).strip():
            raise RuntimeError("owned resources remain")
    disk = bench.storage_sample(root)
    ingestion.save(
        bench.ROOT / "HEADER_CORRUPTION_RESULTS.json",
        {
            "source_directory": str(root.relative_to(bench.ROOT)),
            "result": result,
            "environment": environment,
            "image_id": next(iter(images)),
            "cleanup_verified": True,
            "retained_case_host_disk": {
                k: disk[k] for k in ("logical_bytes", "allocated_bytes")
            },
            "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    print("Published header diagnostic and FAILED unsafe-prefix recovery")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.publish:
            publish(args.case)
        else:
            run(args.case)
