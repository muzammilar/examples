"""Concurrent native Spark rewrite with independently journaled DuckDB hot/cold tasks."""

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid

import compactor_control
import duckdb_crash as live
import duckdb_dependencies as dependencies
import recovery_cleanup as cleanup
import recovery_control as control

artifacts = live.artifacts
APPEND_LIMIT = 100000
COLD_BATCH = 1000


def parent(rows):
    identifier = os.environ.get("BASE_CASE", "")
    run_id = uuid.UUID(identifier)
    if str(run_id) != identifier:
        raise ValueError("canonical healthy baseline UUID required")
    case = cleanup.regular(
        cleanup.results_root() / "duckdb-maintenance" / identifier,
        cleanup.results_root(),
    )
    result, invocation = [
        cleanup.load(case / name, case) for name in ("result.json", "invocation.json")
    ]
    metadata = cleanup.load(case / "authoritative-catalog.json", case)["metadata"]
    if (
        result.get("passed") is not True
        or invocation.get("rows") != rows
        or rows != 2000000
        or invocation.get("table") != "duck_compact_" + run_id.hex
    ):
        raise RuntimeError("require the passed two-million-row healthy native baseline")
    acks = [cleanup.load(path, case) for path in sorted(case.glob("ack-*.json"))]
    intervals = sorted((int(row[0]["first_id"]), int(row[0]["end_id"])) for row in acks)
    if intervals != [(i, i + 100000) for i in range(0, rows, 100000)]:
        raise RuntimeError("inherited acknowledged seed intervals differ")
    return {
        "parent_case": identifier,
        "table": invocation["table"],
        "table_uuid": metadata["table-uuid"],
        "snapshot_id": metadata["current-snapshot-id"],
        "seed_rows": rows,
        "maximum_append_rows": APPEND_LIMIT,
        "parent_receipt_sha256": hashlib.sha256(
            (case / "result.json").read_bytes()
        ).hexdigest(),
    }


def read_oracle(table, maximum, seed_rows, acknowledged_batches):
    invalid = f"event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR event_id<0 OR event_id>={maximum} OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=lower(hex(SHA256(toString(event_id))))"
    acknowledged = ",".join(str(v) for v in acknowledged_batches) or "-1"
    return f"SELECT sum(n) AS rows,sum(u) AS unique_ids,sum(bad) AS invalid_bodies,countIf(n!=1000 OR u!=1000) AS partial_batches,sumIf(n,batch<{seed_rows // COLD_BATCH} OR batch IN ({acknowledged})) AS acknowledged_rows FROM (SELECT intDiv(event_id,1000) AS batch,count() AS n,uniqExact(event_id) AS u,countIf({invalid}) AS bad FROM lake.`lab.{table}` GROUP BY batch) LIMIT 1 SETTINGS use_iceberg_metadata_files_cache=0,iceberg_metadata_staleness_ms=0,max_execution_time=15,max_rows_to_read=30000000,max_bytes_to_read=4294967296,max_memory_usage=805306368,timeout_before_checking_execution_speed=0"


def check_read(value, minimum, maximum):
    if not (
        minimum <= value["rows"] <= maximum
        and value["rows"] == value["unique_ids"]
        and value["invalid_bodies"] == 0
        and value["partial_batches"] == 0
        and value["acknowledged_rows"] == minimum
    ):
        raise RuntimeError(
            "cold reader observed missing, wrong, duplicate or partial data"
        )


def call_overlap(native, clocks, events):
    low = min(v["offset_low"] for v in clocks)
    high = max(v["offset_high"] for v in clocks)
    start = native["rewrite_started_ns"] - low
    end = native["rewrite_ended_ns"] - high
    return (
        start,
        end,
        [
            v
            for v in events
            if v["status"] == "ok" and v["started_ns"] >= start and v["ended_ns"] <= end
        ],
    )


def audit_journals(root):
    errors = root / "cold-actor-errors.json"
    if errors.exists() and cleanup.load(errors, root):
        raise RuntimeError("late cold actor failure recorded")
    config = cleanup.load(root / "concurrent-input.json", root)
    histories = cleanup.load(root / "cold-history.json", root)
    if len(histories) >= APPEND_LIMIT // COLD_BATCH:
        raise RuntimeError("cold writer input exhausted before finalization")
    for role in ("archive", "cold-reader"):
        intents = {
            p.name.removesuffix("-intent.json")
            for p in root.glob(role + "-*-intent.json")
        }
        results = {
            p.name.removesuffix("-result.json")
            for p in root.glob(role + "-*-result.json")
        }
        if not intents or intents != results:
            raise RuntimeError("incomplete cold operation journal")
        for stem in results:
            result = cleanup.load(root / (stem + "-result.json"), root)
            if result.get("native_client_fenced") is not True:
                raise RuntimeError("cold client fencing not proved")
            if role == "cold-reader" and result["status"] == "ok":
                path = root / f"cold-read-{result['sequence']:06d}-proof.json"
                if not path.exists():
                    raise RuntimeError("successful cold read has no body proof")
                proof = cleanup.load(path, root)
                check_read(
                    proof["body"],
                    proof["minimum_acked_rows_at_start"],
                    config["seed_rows"] + APPEND_LIMIT,
                )
    if histories != [
        cleanup.load(root / f"archive-{v['sequence']:06d}-result.json", root)
        for v in histories
    ]:
        raise RuntimeError("cold witness history differs from durable results")
    return {
        "cold_operations_complete": True,
        "successful_cold_reads_have_body_proofs": True,
        "actor_error_report_available": errors.exists(),
    }


def first_attempt_rates(history):
    rates = {}
    for role in ("writer", "reader", "archive", "cold-reader"):
        selected = [v for v in history if v["role"] == role]
        ok = sum(v["status"] == "ok" for v in selected)
        rates[role] = {
            "attempted": len(selected),
            "ok": ok,
            "failed_or_ambiguous": len(selected) - ok,
            "success_rate": ok / len(selected) if selected else 0.0,
        }
    return rates


def drive(root, hot_table, rows, identifier, query, sampler):
    config = parent(rows)
    storage = control.service("storage")
    before = dependencies.catalog(storage, config["table"])
    if (
        before["metadata"]["table-uuid"],
        before["metadata"].get("current-snapshot-id"),
    ) != (config["table_uuid"], config["snapshot_id"]):
        raise RuntimeError("healthy parent changed; review its history before reuse")
    control.save(root / "concurrent-input.json", config)
    control.save(root / "parent-catalog.json", before)
    control.save_text(
        root / "spark_concurrent_job.py",
        (control.ROOT / "projects/duckdb-clickhouse/clickhouse-scanner/scripts/connector_concurrent_job.py").read_text(),
    )
    stop, lock = threading.Event(), threading.Lock()
    cold_history, read_checks, failures = [], [], []
    cold_table = config["table"]

    def operation(request, transition, progress):
        def cold_writer():
            try:
                sequence = 0
                while not stop.is_set():
                    if sequence * COLD_BATCH >= APPEND_LIMIT:
                        raise RuntimeError(
                            "cold writer exhausted its bounded input during maintenance"
                        )
                    first = rows + sequence * COLD_BATCH
                    statement = f"INSERT INTO lake.`lab.{cold_table}` SELECT toInt64(number),toInt32(number%10000),toInt64(number%10000+1),lower(hex(SHA256(toString(number)))) FROM numbers({first},{COLD_BATCH})"
                    result = request(
                        "archive",
                        sequence,
                        "CALL clickhouse_execute('ch','" + statement + "');",
                        first,
                        first + COLD_BATCH,
                    )
                    with lock:
                        cold_history.append(result)
                    sequence += 1
                    stop.wait(0.25)
            except BaseException as error:
                with lock:
                    failures.append(repr(error))
                stop.set()

        def cold_reader():
            try:
                sequence = 0
                while not stop.is_set():
                    with lock:
                        acknowledged = [
                            v["first"] // COLD_BATCH
                            for v in cold_history
                            if v["status"] == "ok"
                        ]
                        minimum = rows + len(acknowledged) * COLD_BATCH
                    sql = read_oracle(
                        cold_table, rows + APPEND_LIMIT, rows, acknowledged
                    )
                    result = request(
                        "cold-reader",
                        sequence,
                        "SELECT * FROM clickhouse_query('ch','"
                        + sql.replace("'", "''")
                        + "');",
                    )
                    if result["status"] == "ok":
                        output = json.loads(
                            (
                                root / f"cold-reader-{sequence:06d}-stdout.json"
                            ).read_text()
                        )
                        if len(output) != 1:
                            raise RuntimeError("cold body summary shape differs")
                        value = {k: int(v) for k, v in output[0].items()}
                        check_read(value, minimum, rows + APPEND_LIMIT)
                        control.save(
                            root / f"cold-read-{sequence:06d}-proof.json",
                            {"minimum_acked_rows_at_start": minimum, "body": value},
                        )
                        with lock:
                            read_checks.append(result)
                    sequence += 1
                    stop.wait(0.25)
            except BaseException as error:
                with lock:
                    failures.append(repr(error))
                stop.set()

        def cold_progress(since, count=2, timeout=45):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with lock:
                    if failures:
                        raise RuntimeError("cold actor failed: " + repr(failures))
                    ready = (
                        sum(
                            v["started_ns"] >= since and v["status"] == "ok"
                            for v in cold_history
                        )
                        >= count
                        and sum(v["started_ns"] >= since for v in read_checks) >= count
                    )
                if ready:
                    return
                time.sleep(0.1)
            raise TimeoutError("no independent cold read/write progress")

        threads = [
            threading.Thread(target=cold_writer),
            threading.Thread(target=cold_reader),
        ]
        compactor = None
        try:
            transition("maintenance-before")
            started = time.time_ns()
            for thread in threads:
                thread.start()
            cold_progress(started)
            env = control.DOCKER_ENV | {
                "RESULTS_DIR": str(root.relative_to(control.ROOT))
            }
            name = "iceberg-concurrent-compactor-" + root.name.replace("-", "")
            control.save(root / "compactor-planned.json", {"name": name})
            compactor = control.call(
                control.COMPOSE
                + [
                    "run",
                    "-d",
                    "--no-deps",
                    "--name",
                    name,
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--entrypoint",
                    "/bin/sh",
                    "-e",
                    "COMPACTOR_S3_ENDPOINT=http://storage:8333",
                    "recovery-compactor",
                    "-c",
                    'while ! test -f /results/spark-release; do sleep .1; done; /opt/spark/bin/spark-submit --master local[2] --driver-memory 1g --conf spark.jars.ivy=/tmp/ivy --conf spark.driver.extraJavaOptions=-Duser.home=/tmp /results/spark_concurrent_job.py; code=$?; printf \'%s\\n\' "$code" > /results/spark-exit.txt; while ! test -f /results/spark-finish; do sleep .1; done; exit "$code"',
                ],
                env=env,
            ).strip()
            value = control.owned(compactor, "recovery-compactor")
            control.check_caps(value, "recovery-compactor")
            control.check_mounts(value, root)
            control.check_networks(
                value,
                {
                    n["NetworkID"]
                    for n in storage["NetworkSettings"]["Networks"].values()
                },
            )
            compactor_control.helper_sources(
                value, "recovery-compactor", {"source_sha256": control.sources()}
            )
            control.save(root / "compactor-started.json", control.state(value))
            sampler.bind("spark-concurrent", compactor, "/results")
            control.wait_coverage(sampler, ["spark-concurrent"], time.time_ns())
            clocks = []

            def clock():
                lower = time.time_ns()
                native = int(
                    control.call(
                        [
                            "docker",
                            "exec",
                            compactor,
                            "python3",
                            "-c",
                            "import time; print(time.time_ns())",
                        ]
                    ).strip()
                )
                upper = time.time_ns()
                clocks.append(
                    {"offset_low": native - upper, "offset_high": native - lower}
                )

            clock()
            transition("maintenance-call")
            control.save_text(root / "spark-release", "release\n")
            deadline = time.monotonic() + 90
            while not (root / "spark-exit.txt").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("native concurrent rewrite deadline")
                if stop.is_set():
                    raise RuntimeError(
                        "cold workload stopped before native rewrite completion"
                    )
                if not control.owned(compactor, "recovery-compactor")["State"][
                    "Running"
                ]:
                    raise RuntimeError(
                        "native compactor exited before its status report"
                    )
                time.sleep(0.2)
            clock()
            control.save(root / "clock-brackets.json", clocks)
            native = cleanup.load(root / "concurrent-native-result.json", root)
            if (
                native.get("passed") is not True
                or (root / "spark-exit.txt").read_text().strip() != "0"
            ):
                raise RuntimeError("native concurrent rewrite failed")
            transition("maintenance-after")
            finished = time.time_ns()
            cold_progress(finished)
            progress("maintenance-after", minimum=4)
            with lock:
                start, end, overlapping = call_overlap(native, clocks, cold_history)
                if failures:
                    raise RuntimeError(repr(failures))
            control.save(
                root / "rewrite-overlap.json",
                {
                    "conservative_host_start_ns": start,
                    "conservative_host_end_ns": end,
                    "cold_acks_wholly_inside_call": overlapping,
                },
            )
            if not overlapping:
                raise RuntimeError(
                    "cold writers live, but native CALL overlap not established"
                )
            # Footer inspection is independent metadata auditing, not application routing.
            sql = [
                "SET extension_directories=['/opt/duckdb/extensions']; LOAD httpfs;",
                "CREATE SECRET audit_s3 (TYPE S3,KEY_ID 'demo',SECRET 'demo',ENDPOINT 'storage:8333',URL_STYLE 'path',USE_SSL false);",
            ]
            for label in ("before", "after"):
                paths = [v["location"] for v in native[label]["file_membership"]]
                if (
                    not paths
                    or len(paths) > 512
                    or any(
                        not p.startswith("s3://warehouse/lab/" + cold_table + "-")
                        or "'" in p
                        for p in paths
                    )
                ):
                    raise RuntimeError("native scan membership escaped audit bounds")
                files = "[" + ",".join("'" + p + "'" for p in paths) + "]"
                sql.append(
                    f"COPY (SELECT count(DISTINCT file_name) AS files,sum(total_compressed_size) AS compressed_bytes,sum(total_uncompressed_size) AS uncompressed_bytes FROM parquet_metadata({files})) TO '/results/{label}-compression.json' (FORMAT JSON,ARRAY true);"
                )
            audit = request("storage-audit", 0, "\n".join(sql))
            if audit["status"] != "ok":
                raise RuntimeError("native rewrite compression audit failed")
        finally:
            stop.set()
            for thread in threads:
                if thread.ident:
                    thread.join(timeout=15)
            if compactor is not None:
                value = control.owned(compactor, "recovery-compactor")
                logs = subprocess.run(
                    ["docker", "logs", compactor],
                    env=control.DOCKER_ENV,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                control.save_text(root / "compactor-stdout.log", logs.stdout)
                control.save_text(root / "compactor-stderr.log", logs.stderr)
                if value["State"]["Running"]:
                    control.save_text(root / "spark-finish", "finish\n")
                    try:
                        control.call(["docker", "wait", compactor], timeout=10)
                    except BaseException:
                        control.call(
                            ["docker", "stop", "--time", "10", compactor], timeout=20
                        )
                value = control.owned(compactor, "recovery-compactor")
                control.save(root / "compactor-ended.json", control.state(value))
                if value["State"]["Running"] or value["State"]["Pid"]:
                    raise RuntimeError("native compactor not fenced")
            if any(t.is_alive() for t in threads):
                raise RuntimeError("cold actor did not stop")
            control.save(root / "cold-history.json", cold_history)
            control.save(root / "cold-reader-history.json", read_checks)
            control.save(root / "cold-actor-errors.json", failures)
            if failures:
                raise RuntimeError("late cold actor failure: " + repr(failures))

    return live.drive(
        root,
        hot_table,
        rows,
        identifier,
        query,
        sampler,
        operation=operation,
        seed_batch=100000,
    )


def verify(root, hot_table, rows, query):
    journals = audit_journals(root)
    hot = live.verify(root, hot_table, rows, query)
    hot["scope"] = (
        "Independent hot DuckDB workload reconciliation; no node crash injected in this maintenance case."
    )
    config = cleanup.load(root / "concurrent-input.json", root)
    history = cleanup.load(root / "cold-history.json", root)
    client = [
        "docker",
        "exec",
        query["Id"],
        "clickhouse-client",
        "--password",
        "local-reader",
        "--query",
    ]
    table = config["table"]
    active = f"SELECT count() FROM system.processes WHERE startsWith(query,'INSERT INTO lake.') AND position(query,'{table}')>0 LIMIT 1 SETTINGS max_execution_time=10 FORMAT TSV"
    deadline = time.monotonic() + 120
    while control.call(client + [active]).strip() != "0":
        if time.monotonic() >= deadline:
            raise TimeoutError("cold archive still active after client fencing")
        time.sleep(0.2)
    sql = f"SELECT intDiv(event_id,1000) AS batch,count() AS rows,uniqExact(event_id) AS unique_ids,min(event_id) AS first_id,max(event_id) AS last_id,countIf(event_id<0 OR event_id>={rows + APPEND_LIMIT} OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=lower(hex(SHA256(toString(event_id))))) AS invalid_bodies FROM lake.`lab.{table}` GROUP BY batch ORDER BY batch LIMIT 3000 SETTINGS max_execution_time=120,max_memory_usage=805306368,max_rows_to_read=30000000,max_bytes_to_read=4294967296,timeout_before_checking_execution_speed=0 FORMAT JSONEachRow"
    control.save_text(root / "cold-reconcile.sql", sql)
    raw = control.call(client + [sql], timeout=130)
    control.save_text(root / "cold-reconcile.jsonl", raw)
    cold = live.reconcile(
        [json.loads(line) for line in raw.splitlines()],
        [{"first": 0, "end": rows, "status": "ok"}] + history,
        batch_size=COLD_BATCH,
    )
    control.save(
        root / "cold-catalog-final.json",
        dependencies.catalog(control.service("storage"), table),
    )
    result = {
        "journals": journals,
        "hot": hot,
        "cold": cold,
        "production_qualified": False,
        "scope": "Full million-row rewrite with measured native CALL overlap and inherited/new witnesses; no snapshot expiration, vacuum, fault injection or capacity qualification.",
    }
    result["first_attempt_rates"] = first_attempt_rates(
        cleanup.load(root / "history.json", root)
    )
    result["success_threshold"] = 0.95
    result["availability_passed"] = all(
        v["success_rate"] >= 0.95 for v in result["first_attempt_rates"].values()
    )
    control.save(root / "verification-checks.json", result)
    if not result["availability_passed"]:
        raise RuntimeError(
            "maintenance availability below 0.95; integrity checks are preserved separately"
        )
    return result


def recheck(identifier):
    import cold_readback
    from recovery_resources import ResourceSampler

    case_id = uuid.UUID(identifier)
    if str(case_id) != identifier:
        raise ValueError("canonical case UUID required")
    context = control.local_docker()
    cold_readback.original_plane()
    case = cleanup.regular(
        cleanup.results_root() / "duckdb-connector-concurrent" / identifier,
        cleanup.results_root(),
    )
    original = cleanup.load(case / "invocation.json", case)
    if (
        original["mode"] != "connector-concurrent"
        or original["table"] != "duck_compact_" + case_id.hex
    ):
        raise RuntimeError("recheck case identity differs")
    root = case / "rechecks" / str(uuid.uuid4())
    root.mkdir(parents=True)
    inputs = [
        "history.json",
        "cold-history.json",
        "cold-reader-history.json",
        "concurrent-input.json",
    ]
    if (case / "cold-actor-errors.json").exists():
        inputs.append("cold-actor-errors.json")
    for pattern in (
        "archive-*-intent.json",
        "archive-*-result.json",
        "cold-reader-*-intent.json",
        "cold-reader-*-result.json",
        "cold-read-*-proof.json",
    ):
        inputs.extend(p.name for p in case.glob(pattern))
    hashes = {}
    for name in sorted(inputs):
        path = cleanup.regular(case / name, case)
        data = path.read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        control.save_text(root / name, data.decode())
    control.save(
        root / "recheck-inputs.json",
        {
            "context": context,
            "case": identifier,
            "input_sha256": hashes,
            "source_sha256": control.sources(),
        },
    )
    storage, query = control.service("storage"), control.service("clickhouse")
    sampler = ResourceSampler(root, env=control.DOCKER_ENV)
    sampler.bind("storage", storage["Id"], "/data")
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    sampler.start()
    result, failure = None, None
    try:
        result = verify(root, original["table"], original["rows"], query)
    except BaseException as error:
        failure = error
    finally:
        try:
            _, resources = sampler.close()
            control.save(root / "resource-summary.json", resources)
        except BaseException as error:
            failure = failure or error
        control.save(
            root / "recheck-result.json",
            {
                "passed": failure is None,
                "checks": result,
                "error": repr(failure) if failure else None,
                "scope": "Read-only durable-journal audit and complete current hot/cold data re-verification, not a new rewrite trial.",
            },
        )
    print(root.relative_to(control.ROOT), flush=True)
    if failure:
        raise failure


if __name__ == "__main__":
    import argparse
    import fcntl
    import cold_storage

    parser = argparse.ArgumentParser(
        description="Read-only replay of a concurrent compaction witness"
    )
    parser.add_argument("case")
    args = parser.parse_args()
    with cold_storage.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recheck(args.case)
