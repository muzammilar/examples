"""Matched append paths with full-body verification and per-trial resource costs."""

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import resource
import statistics
import time
import uuid
import zlib

import experiment as bench


def save(path, value):
    bench.save(path, value)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def trade(identity):
    return [
        identity,
        identity % 10000,
        identity % 256,
        identity % 30,
        1 + identity % 100,
        100 + identity % 9901,
    ]


def insert(engine, rows):
    if engine == "duckdb":
        return "INSERT INTO trades VALUES " + ",".join(
            "(" + ",".join(map(str, row)) + ")" for row in rows
        )
    columns = " ".join(
        "[" + " ".join(str(row[c]) for row in rows) + "]" for c in range(6)
    )
    return "(insert 'trades (list " + columns + "))"


def point(engine, row):
    query = (
        ("SELECT id,customer,symbol,day,qty,price FROM trades WHERE id=" + str(row[0]))
        if engine == "duckdb"
        else "(select {from: trades where: (== id " + str(row[0]) + ")})"
    )
    return bench.serialized([row]), query


def export_query(engine):
    if engine == "duckdb":
        return "COPY (SELECT id,customer,symbol,day,qty,price FROM trades ORDER BY id) TO '/verification/trades.csv' (HEADER TRUE, DELIMITER ',')"
    return '(.csv.write (select {from: trades asc: id}) "/verification/trades.csv")'


def oracle(first, count):
    source = bench.RESULTS / "data/trades.csv"
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    appended = "".join(
        ",".join(map(str, trade(i))) + "\n" for i in range(first, first + count)
    ).encode()
    digest.update(appended)
    return {
        "sha256": digest.hexdigest(),
        "bytes": source.stat().st_size + len(appended),
        "rows": first + count,
    }


def write_plan(path, engine, method, first, count, batch_size):
    with (bench.RESULTS / "data/trades.csv").open() as source:
        reader = csv.reader(source)
        next(reader)
        original = list(map(int, next(reader)))
    entries = [
        ("load", "load" + str(i), "", q) for i, q in enumerate(bench.queries(engine)[0])
    ]
    expected, query = point(engine, original)
    entries.append(("probe", "old-row", expected, query))
    for i, (start, size) in enumerate(
        ((-1, 1), (first, 0), (first, 10001), (2**63 - 1, 2))
    ):
        entries.append(
            (
                "reject-columnar" if method == "columnar" else "reject-append",
                "bounds-" + str(i),
                "",
                f"{start} {size}",
            )
        )
    batches = list(range(first, first + count, batch_size))
    for index, start in enumerate(batches):
        size = min(batch_size, first + count - start)
        if method in {"native", "columnar"}:
            entries.append(
                (
                    "columnar" if method == "columnar" else "append",
                    "batch-" + str(index),
                    "",
                    f"{start} {size}",
                )
            )
        else:
            entries.append(
                (
                    "write",
                    "batch-" + str(index),
                    "",
                    insert(engine, [trade(i) for i in range(start, start + size)]),
                )
            )
        if index == (len(batches) - 1) // 2:
            expected, query = point(engine, trade(start + size - 1))
            entries.append(("probe", "middle-readback", expected, query))
    expected, query = point(engine, trade(first + count - 1))
    entries.extend(
        (
            ("probe", "final-readback", expected, query),
            ("export", "full-body", "", export_query(engine)),
        )
    )
    with path.open("x") as stream:
        for fields in entries:
            stream.write("\t".join(fields) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return len(entries) + 1  # The driver emits a separately timed ingestion window.


def verify_export(path, expected):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("missing or unsafe full-body export")
    digest = hashlib.sha256()
    compressor = zlib.compressobj(1, zlib.DEFLATED, 31)
    compressed = 0
    compression_wall = compression_cpu = 0
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
        while block := stream.read(1024 * 1024):
            digest.update(block)
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
            compressed += len(compressor.compress(block))
            compression_wall += time.perf_counter_ns() - wall
            compression_cpu += time.process_time_ns() - cpu
        wall, cpu = time.perf_counter_ns(), time.process_time_ns()
        compressed += len(compressor.flush())
        compression_wall += time.perf_counter_ns() - wall
        compression_cpu += time.process_time_ns() - cpu
    stat = path.stat()
    actual = {"sha256": digest.hexdigest(), "bytes": stat.st_size}
    if actual["sha256"] != expected["sha256"] or actual["bytes"] != expected["bytes"]:
        raise RuntimeError(
            "full-body export differs from the independent CSV oracle: "
            + json.dumps(actual)
        )
    return actual | {
        "verified_rows": expected["rows"],
        "allocated_bytes": stat.st_blocks * 512,
        "compression": {
            "codec": "gzip",
            "level": 1,
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
            "uncompressed_bytes": stat.st_size,
            "compressed_bytes": compressed,
            "ratio": stat.st_size / compressed,
            "wall_ns": compression_wall,
            "cpu_ns": compression_cpu,
            "scope": "Host-side compressibility of the verified CSV witness, streamed into a byte counter; not native engine compression. Timers cover compression calls, excluding file reads/hash/fsync.",
        },
    }


def write_summary(result, count):
    writes = [
        r for r in result["records"] if r["phase"] in {"write", "append", "columnar"}
    ]
    windows = [
        r
        for r in result["records"]
        if r["phase"] == "window" and r["query"] == "ingestion"
    ]
    if not writes or len(windows) != 1:
        raise RuntimeError("missing write/window evidence")
    latencies = sorted(r["elapsed_ns"] for r in writes)
    window = windows[0]
    if window["elapsed_ns"] < sum(latencies):
        raise RuntimeError("write window is shorter than native calls")
    return {
        "acknowledged_rows": count,
        "requests": len(writes),
        "window_seconds": window["elapsed_ns"] / 1e9,
        "rows_per_second": count / (window["elapsed_ns"] / 1e9),
        "requests_per_second": len(writes) / (window["elapsed_ns"] / 1e9),
        "window_cpu_seconds": window["cpu_ns"] / 1e9,
        "request_p50_ms": statistics.median(latencies) / 1e6,
        "request_p95_ms": latencies[math.ceil(len(latencies) * 0.95) - 1] / 1e6,
        "request_p99_ms": latencies[math.ceil(len(latencies) * 0.99) - 1] / 1e6,
        "native_call_seconds": sum(latencies) / 1e9,
        "scope": "Measured first call start to last call finish, including intervening plan handling/logging and the middle probe when multiple requests exist. Import, initial/final read and export are separate. Request percentiles with one/few batches do not establish tails.",
    }


def selected(name, default, allowed):
    values = os.environ.get(name, default).split(",")
    if not values or len(values) != len(set(values)) or not set(values) <= set(allowed):
        raise ValueError("invalid " + name)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("test", "benchmark"))
    action = parser.parse_args().action
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller_started = time.monotonic()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        fixture = bench.prepare()
        count = bench.integer("MUTATION_ROWS", 10000, 100, 100000)
        threads = bench.integer("THREADS", 1, 1, 4)
        trials = bench.integer("TRIALS", 3, 3, 10) if action == "benchmark" else 1
        batches = [
            int(s) for s in os.environ.get("BATCH_SIZES", "1,100,1000,10000").split(",")
        ]
        if (
            not batches
            or len(set(batches)) != len(batches)
            or any(not 1 <= n <= 10000 for n in batches)
        ):
            raise ValueError("BATCH_SIZES must be unique and within 1..10000")
        methods = selected("METHODS", "query,native", ("query", "native", "columnar"))
        languages = selected("LANGUAGES", "python,cpp,rust", ("python", "cpp", "rust"))
        root = bench.RESULTS / "ingestion" / uuid.uuid4().hex
        root.mkdir(parents=True)
        (bench.RESULTS / "plans").mkdir(exist_ok=True)
        expected = oracle(fixture["rows"], count)
        setup = {
            "fixture": fixture,
            "mutation_rows": count,
            "threads": threads,
            "trials": trials,
            "batches": batches,
            "methods": methods,
            "languages": languages,
            "expected_export": expected,
            "contract": json.loads(
                (bench.ROOT / "config/ingestion-contract.json").read_text()
            ),
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_revision": bench.capture(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "sources": json.loads((bench.ROOT / "config/sources.json").read_text()),
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in bench.ROOT.rglob("*")
                if p.is_file()
                and not any(
                    part in {"results", ".build", "target", "__pycache__"}
                    for part in p.relative_to(bench.ROOT).parts
                )
            },
        }
        save(root / "setup.json", setup)
        configurations = [
            (language, engine, method, batch)
            for method in methods
            for batch in batches
            for language in languages
            for engine in ("rayforce", "duckdb")
        ]
        completed = []
        for trial in range(1, trials + 1):
            for language, engine, method, batch in configurations[
                :: (-1 if trial % 2 == 0 else 1)
            ]:
                case = root / uuid.uuid4().hex
                case.mkdir()
                witness = case / "witness"
                witness.mkdir()
                metadata = {
                    "language": language,
                    "engine": engine,
                    "method": method,
                    "batch_size": batch,
                    "trial": trial,
                }
                save(case / "case.json", metadata)
                plan = bench.RESULTS / "plans" / (engine + "-" + case.name + ".tsv")
                records = write_plan(
                    plan, engine, method, fixture["rows"], count, batch
                )
                print(json.dumps({"case": case.name} | metadata), flush=True)
                try:
                    result = bench.run_case(
                        language,
                        engine,
                        trial,
                        case,
                        0,
                        threads,
                        record_count=records,
                        verification_path=witness,
                    )
                    phases = {
                        phase: {
                            "operations": sum(
                                r["phase"] == phase for r in result["records"]
                            ),
                            "wall_seconds": sum(
                                r["elapsed_ns"]
                                for r in result["records"]
                                if r["phase"] == phase
                            )
                            / 1e9,
                            "cpu_seconds": sum(
                                r["cpu_ns"]
                                for r in result["records"]
                                if r["phase"] == phase
                            )
                            / 1e9,
                        }
                        for phase in {r["phase"] for r in result["records"]}
                    }
                    report = metadata | {
                        "case": case.name,
                        "writes": write_summary(result, count),
                        "native": {k: v for k, v in result.items() if k != "records"},
                        "phases": phases,
                        "snapshot": verify_export(witness / "trades.csv", expected),
                    }
                    save(case / "result.json", report)
                    (witness / "trades.csv").unlink()
                    save(
                        case / "export-cleanup.json",
                        {
                            "passed": not (witness / "trades.csv").exists(),
                            "retained": "SHA256, byte counts, exact oracle and compression/resource summaries",
                        },
                    )
                    completed.append(report)
                    print(
                        json.dumps(
                            {
                                "case": case.name,
                                "passed": True,
                                "rows_per_second": report["writes"]["rows_per_second"],
                            }
                        ),
                        flush=True,
                    )
                except Exception as error:
                    save(case / "failure.json", {"passed": False, "error": str(error)})
                    raise
        after = resource.getrusage(resource.RUSAGE_SELF)
        children_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        save(
            root / "result.json",
            {
                "passed": True,
                "cases": completed,
                "controller": {
                    "wall_seconds": time.monotonic() - controller_started,
                    "user_cpu_seconds": after.ru_utime - usage.ru_utime,
                    "system_cpu_seconds": after.ru_stime - usage.ru_stime,
                    "child_cli_user_cpu_seconds": children_after.ru_utime
                    - children_before.ru_utime,
                    "child_cli_system_cpu_seconds": children_after.ru_stime
                    - children_before.ru_stime,
                    "process_lifetime_peak_rss_bytes": after.ru_maxrss
                    * (1 if os.uname().sysname == "Darwin" else 1024),
                    "scope": "Host plan generation, full-body hashing and off-path CSV compression; separate from engine containers. Peak RSS includes setup/oracle creation.",
                },
            },
        )
        print("Evidence:", root, flush=True)


if __name__ == "__main__":
    main()
