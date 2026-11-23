"""Preserve row-level evidence when a streamed connector predicate is wrong."""

import duckdb_clickhouse as connector
import duckdb_maintenance as maintenance
import recovery_control as control


def read_artifacts(rows):
    if not 1000 <= rows <= 10000000:
        raise ValueError("bounded generated read fixture required")
    generated = f"SELECT toInt64(number) AS event_id,toInt32(number%10000) AS customer_id,toInt64(number%10000+1) AS amount_cents,lower(hex(SHA256(toString(number)))) AS payload_sha256 FROM numbers({rows}) LIMIT {rows}"
    remote = "clickhouse_query('ch','" + generated + "')"
    bad = f"event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR event_id<0 OR event_id>={rows} OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=sha256(CAST(event_id AS VARCHAR))"
    local = f"(SELECT i::BIGINT AS event_id,(i%10000)::INTEGER AS customer_id,i%10000+1 AS amount_cents,sha256(CAST(i AS VARCHAR)) AS payload_sha256 FROM range({rows}) t(i))"
    sql = connector.connection(rows, 100000)

    def copy(query, name):
        sql.append(
            f"COPY ({query}) TO '/results/{name}.json' (FORMAT JSON,ARRAY true);"
        )

    for threads in (1, 2):
        sql.append(f"SET threads={threads};")
        copy(maintenance.oracle_query(local, rows, 10000), f"local-{threads}")
        copy(maintenance.oracle_query(remote, rows, 10000), f"stream-{threads}")
        sql.append(
            f"CREATE TEMP TABLE evaluated AS SELECT *,({bad}) AS ingress_bad FROM {remote};"
        )
        copy(maintenance.oracle_query("evaluated", rows, 10000), f"stored-{threads}")
        copy(
            "SELECT count(*) AS rows,count(*) FILTER (WHERE ingress_bad) AS ingress_bad_rows,count(*) FILTER (WHERE ingress_bad IS NULL) AS null_flags FROM evaluated",
            f"flags-{threads}",
        )
        copy(
            f"SELECT event_id,customer_id,amount_cents,payload_sha256,ingress_bad,({bad}) AS recomputed_bad FROM evaluated WHERE ingress_bad LIMIT 20",
            f"samples-{threads}",
        )
        sql.append("DROP TABLE evaluated;")
    copy(
        "SELECT version() AS engine,extension_version,loaded,current_setting('allow_unsigned_extensions') AS unsigned_allowed FROM duckdb_extensions() WHERE extension_name='clickhouse_scanner'",
        "connector",
    )
    return {"run.sql": "\n".join(sql) + "\n", "run.sh": connector.shell()}


def verify_read(root, rows):
    checks, failures = {}, []
    for threads in (1, 2):
        for stage in ("local", "stream", "stored"):
            name = f"{stage}-{threads}"
            value = maintenance.single(root, name)
            checks[name] = value
            try:
                maintenance.check_rows(value, rows)
            except RuntimeError:
                failures.append(name)
        flags = maintenance.single(root, f"flags-{threads}")
        checks[f"flags-{threads}"] = flags
        if flags != {"rows": rows, "ingress_bad_rows": 0, "null_flags": 0}:
            failures.append(f"flags-{threads}")
    control.save(
        root / "isolation-checks.json",
        {
            "checks": checks,
            "failed_stages": failures,
            "root_cause_assigned": False,
            "production_qualified": False,
        },
    )
    if failures:
        raise RuntimeError("wrong-answer isolation failed: " + ", ".join(failures))
    return {"checks": checks, "production_qualified": False}
