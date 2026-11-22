"""DuckDB SQL application front end for ClickHouse hot and Iceberg cold data."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

import duckdb_maintenance as maintenance
import recovery_cleanup as cleanup
import recovery_control as control

REVISION = "ce337c48c1"
BINARY_SHA = "e32c23138bc7eb620e428bbc6f9b62d7984cdc9d7e94cc19299ae18d852cd5ab"
RELATIVE_BINARY = Path("v2.0.1/linux_arm64/clickhouse_scanner.duckdb_extension")


def body_gate(name, rows):
    if (
        name not in {"hot-rows", "cold-rows", "restored-rows"}
        or not 1 <= rows <= 10000000
    ):
        raise ValueError("bounded owned body-gate result required")
    total = rows // 10000 * 50005000 + (rows % 10000) * (rows % 10000 + 1) // 2
    return f"SELECT CASE WHEN (SELECT count(*)=1 AND bool_and(rows={rows} AND unique_ids={rows} AND first_id=0 AND last_id={rows - 1} AND amount_total_cents={total} AND invalid_bodies=0) FROM read_json('/results/{name}.json')) THEN true ELSE error('full-body oracle failed: {name}') END;"


def prepare(root, image):
    """Fetch public signed code separately; the application gets internal networking only."""
    parent = cleanup.results_root() / "connector-install"
    parent.mkdir(exist_ok=True)
    cache = None
    for candidate in sorted(parent.iterdir()):
        cleanup.regular(candidate, parent)
        receipt = candidate / "receipt.json"
        if receipt.exists() and cleanup.load(receipt, candidate).get("passed") is True:
            cache = candidate
            break
    if cache is None:
        cache = parent / str(uuid.uuid4())
        cache.mkdir()
        sql = """SET extension_directories=['/results/extensions'];
INSTALL clickhouse_scanner FROM community;
LOAD clickhouse_scanner FROM community;
COPY (SELECT version() AS engine,extension_version,loaded,current_setting('allow_unsigned_extensions') AS unsigned_allowed FROM duckdb_extensions() WHERE extension_name='clickhouse_scanner') TO '/results/installed.json' (FORMAT JSON,ARRAY true);
"""
        control.save_text(cache / "install.sql", sql)
        name = "iceberg-connector-install-" + cache.name
        identifier = control.call(
            [
                "docker",
                "create",
                "--name",
                name,
                "--label",
                "org.examples.connector-install=" + cache.name,
                "--network",
                "bridge",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--cpus",
                "1",
                "--memory",
                "1g",
                "--memory-swap",
                "1g",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--mount",
                f"type=bind,src={cache},dst=/results",
                "--env",
                "AWS_EC2_METADATA_DISABLED=true",
                "--entrypoint",
                "/bin/sh",
                image["Id"],
                "-c",
                "exec duckdb -batch -bail :memory: < /results/install.sql",
            ]
        ).strip()

        def inspect():
            (value,) = json.loads(control.call(["docker", "inspect", identifier]))
            if (
                value["Id"] != identifier
                or value["Image"] != image["Id"]
                or value["Name"] != "/" + name
                or value["Config"]["Labels"].get("org.examples.connector-install")
                != cache.name
            ):
                raise RuntimeError("connector installer identity differs")
            control.check_mounts(value, cache)
            h = value["HostConfig"]
            if (
                h["NanoCpus"] != 10**9
                or h["Memory"] != 1073741824
                or h["MemorySwap"] != 1073741824
                or h["NetworkMode"] != "bridge"
                or not h["ReadonlyRootfs"]
            ):
                raise RuntimeError("connector installer isolation/budget differs")
            return value

        failure = None
        try:
            control.save(cache / "created.json", control.state(inspect()))
            control.call(["docker", "start", identifier])
            control.call(["docker", "wait", identifier], timeout=120)
            value = inspect()
            if (
                value["State"]["Running"]
                or value["State"]["Pid"]
                or value["State"]["ExitCode"] != 0
                or value["State"]["OOMKilled"]
            ):
                raise RuntimeError("signed connector installation failed")
        except BaseException as error:
            failure = error
        finally:
            value = inspect()  # Runtime inspection errors must not imply fencing.
            if value["State"]["Running"]:
                control.call(["docker", "stop", "--time", "20", identifier])
                value = inspect()
            control.save(cache / "final.json", control.state(value))
            logs = subprocess.run(
                ["docker", "logs", identifier],
                env=control.DOCKER_ENV,
                capture_output=True,
                text=True,
                timeout=20,
            )
            control.save_text(cache / "stdout.log", logs.stdout)
            control.save_text(cache / "stderr.log", logs.stderr)
            if logs.returncode:
                failure = failure or RuntimeError(
                    "connector installer logs unavailable"
                )
            control.save(
                cache / "receipt.json",
                {
                    "passed": failure is None,
                    "error": repr(failure) if failure else None,
                    "image_id": image["Id"],
                    "container_id": identifier,
                },
            )
        if failure:
            raise failure
        if value["State"]["Running"] or value["State"]["Pid"]:
            raise RuntimeError("connector installer is not fenced")
        control.call(["docker", "rm", identifier])
    (installed,) = cleanup.load(cache / "installed.json", cache)
    if installed != {
        "engine": "v2.0.1",
        "extension_version": REVISION,
        "loaded": True,
        "unsigned_allowed": False,
    }:
        raise RuntimeError("signed native connector version differs")
    source = cleanup.regular(cache / "extensions" / RELATIVE_BINARY, cache)
    if cleanup.digest(source) != BINARY_SHA:
        raise RuntimeError("connector artifact changed; review the new revision first")
    manifest = {}
    for relative in (RELATIVE_BINARY, Path(str(RELATIVE_BINARY) + ".info")):
        source = cleanup.regular(cache / "extensions" / relative, cache)
        target = cleanup.regular(root / "community" / relative, root)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
        manifest[str(relative)] = cleanup.digest(source)
        if cleanup.digest(target) != manifest[str(relative)]:
            raise RuntimeError("materialized connector differs")
    control.save(
        root / "connector-artifacts.json",
        {
            "cache": str(cache.relative_to(control.ROOT)),
            "files": manifest,
            "signed_install": installed,
        },
    )


def catalog_request(storage, path, *, body=None, token=None, expected=(200,)):
    args = [
        "docker",
        "exec",
        storage["Id"],
        "curl",
        "-q",
        "--noproxy",
        "*",
        "--proto",
        "=http",
        "--connect-timeout",
        "5",
        "--max-time",
        "15",
        "--max-filesize",
        "8388608",
        "--silent",
        "--show-error",
        "--write-out",
        "\n%{http_code}",
    ]
    if token is not None:
        if (
            not isinstance(token, str)
            or not token
            or any(not 32 < ord(c) < 127 for c in token)
        ):
            raise RuntimeError("invalid local catalog token")
        args += ["--header", "Authorization: Bearer " + token]
    if body is not None:
        content, data = (
            ("application/x-www-form-urlencoded", body)
            if isinstance(body, str)
            else ("application/json", json.dumps(body))
        )
        args += ["--header", "Content-Type: " + content, "--data", data]
    raw = control.call(args + ["http://127.0.0.1:8181/v1/" + path], timeout=20)
    data, _, status = raw.rpartition("\n")
    if (
        not status.isdigit()
        or int(status) not in expected
        or len(data.encode()) > 8388608
    ):
        raise RuntimeError("unexpected local catalog response: " + status)
    return int(status), json.loads(data)


def setup(root, table, storage):
    # Provisioning is an administrative REST operation, not an application-side
    # DuckDB Iceberg connection. Application SQL reaches ClickHouse exclusively.
    schema = {
        "type": "struct",
        "schema-id": 0,
        "fields": [
            {"id": i, "name": name, "type": kind, "required": False}
            for i, (name, kind) in enumerate(
                [
                    ("event_id", "long"),
                    ("customer_id", "int"),
                    ("amount_cents", "long"),
                    ("payload_sha256", "string"),
                ],
                1,
            )
        ],
    }
    _, auth = catalog_request(
        storage,
        "oauth/tokens",
        body="grant_type=client_credentials&client_id=demo&client_secret=demo",
    )
    token = auth["access_token"]
    status, _ = catalog_request(
        storage, "namespaces/lab", token=token, expected=(200, 404)
    )
    if status == 404:
        catalog_request(storage, "namespaces", body={"namespace": ["lab"]}, token=token)
    _, response = catalog_request(
        storage,
        "namespaces/lab/tables",
        body={"name": table, "schema": schema, "properties": {"format-version": "2"}},
        token=token,
        expected=(200, 201),
    )
    if response["metadata"]["format-version"] != 2:
        raise RuntimeError("fresh administrative table is not Iceberg v2")
    control.save(root / "created-catalog.json", response)


def finalize(root, table, storage, rows):
    _, auth = catalog_request(
        storage,
        "oauth/tokens",
        body="grant_type=client_credentials&client_id=demo&client_secret=demo",
    )
    _, response = catalog_request(
        storage, "namespaces/lab/tables/" + table, token=auth["access_token"]
    )
    metadata = response["metadata"]
    before = cleanup.load(root / "created-catalog.json", root)["metadata"]
    current = [
        v
        for v in metadata["snapshots"]
        if v["snapshot-id"] == metadata["current-snapshot-id"]
    ]
    if (
        metadata["table-uuid"] != before["table-uuid"]
        or metadata["format-version"] != 2
        or len(current) != 1
        or int(current[0]["summary"]["total-records"]) != rows
    ):
        raise RuntimeError("published Iceberg pointer/row summary differs")
    control.save(root / "published-catalog.json", response)
    return {
        "table_uuid": metadata["table-uuid"],
        "snapshot_id": metadata["current-snapshot-id"],
        "rows": rows,
    }


def connection(rows, batch):
    settings = f"allow_database_iceberg=1,allow_insert_into_iceberg=1,max_execution_time=120,max_rows_to_read={rows * 3},max_bytes_to_read=4294967296,max_memory_usage=805306368,max_threads=2,max_result_rows={rows + 1},max_result_bytes=536870912,result_overflow_mode=throw,timeout_before_checking_execution_speed=0"
    return [
        "SET extension_directories=['/results/community']; LOAD clickhouse_scanner FROM community;",
        "SET threads=2; SET memory_limit='1GB'; SET temp_directory='/results/spill'; SET max_temp_directory_size='1GB';",
        f"SET ch_insert_block_size={batch}; SET ch_connect_timeout_ms=5000; SET ch_receive_timeout_ms=120000; SET ch_pool_max_connections=2; SET ch_pool_acquire_mode='wait';",
        "CREATE SECRET ch_local (TYPE clickhouse,HOST 'clickhouse',PORT 9000,USER 'default',PASSWORD 'local-reader',DATABASE 'default');",
        f"ATTACH '' AS ch (TYPE clickhouse,SECRET ch_local,SETTINGS '{settings}');",
    ]


def shell():
    return "#!/bin/sh\nset -u\nduckdb -batch -bail -json :memory: < /results/run.sql > /results/native-stdout.log 2> /results/native-stderr.log\ncode=$?\nprintf '%s\\n' \"$code\" > /results/native-exit.txt\ntest \"$code\" -eq 0 || exit 1\nprintf 'complete\\n' > /results/native-complete\nwhile ! test -f /results/native-finish; do sleep .1; done\n"


def read_artifacts(rows):
    if not 1000 <= rows <= 10000000:
        raise ValueError("bounded generated read fixture required")
    sql = connection(rows, 100000)
    generated = f"SELECT toInt64(number) AS event_id,toInt32(number%10000) AS customer_id,toInt64(number%10000+1) AS amount_cents,lower(hex(SHA256(toString(number)))) AS payload_sha256 FROM numbers({rows}) LIMIT {rows}"
    source = "clickhouse_query('ch','" + generated + "')"
    for threads in (1, 2):
        sql += [
            f"SET threads={threads};",
            "SET enable_profiling='json';",
            f"SET profiling_output='/results/threads-{threads}-profile.json';",
            f"COPY ({maintenance.oracle_query(source, rows, 10000)}) TO '/results/threads-{threads}.json' (FORMAT JSON,ARRAY true);",
            "PRAGMA disable_profiling;",
        ]
    sql += [
        "COPY (SELECT version() AS engine,extension_version,loaded,current_setting('allow_unsigned_extensions') AS unsigned_allowed FROM duckdb_extensions() WHERE extension_name='clickhouse_scanner') TO '/results/connector.json' (FORMAT JSON,ARRAY true);"
    ]
    return {"run.sql": "\n".join(sql) + "\n", "run.sh": shell()}


def verify_read(root, rows):
    for threads in (1, 2):
        try:
            maintenance.check_rows(maintenance.single(root, f"threads-{threads}"), rows)
        except RuntimeError as error:
            raise RuntimeError(
                f"wrong-answer regression: {threads} DuckDB scan threads"
            ) from error
    return {
        "generated_rows": rows,
        "one_and_two_thread_bodies_correct": True,
        "production_qualified": False,
    }


def artifacts(table, rows, customers, batch):
    if not re.fullmatch(r"duck_frontend_[0-9a-f]{32}", table) or not (
        1 <= customers <= rows <= 10000000 and 1000 <= batch <= rows
    ):
        raise ValueError("bounded fresh connector fixture required")
    hot = "ch.default." + table
    restored = hot + "_restored"
    cold = "lake.`lab." + table + "`"
    sql = connection(rows, batch)

    def profile(statement, name):
        sql.extend(
            [
                "SET enable_profiling='json';",
                f"SET profiling_output='/results/{name}-profile.json';",
                statement,
                "PRAGMA disable_profiling;",
            ]
        )

    def copy(query, name):
        statement = (
            f"COPY ({query}) TO '/results/{name}.json' (FORMAT JSON,ARRAY true);"
        )
        if name in {"hot-rows", "cold-rows", "restored-rows"}:
            profile(statement, name)
            sql.append(body_gate(name, rows))
        else:
            sql.append(statement)

    def quote(statement):
        return "'" + statement.replace("'", "''") + "'"

    def execute(statement, name=None):
        call = "CALL clickhouse_execute('ch', " + quote(statement) + ");"
        if name:
            profile(call, name)
        else:
            sql.append(call)

    copy(
        "SELECT version() AS engine,extension_version,loaded,current_setting('allow_unsigned_extensions') AS unsigned_allowed FROM duckdb_extensions() WHERE extension_name='clickhouse_scanner'",
        "connector",
    )
    copy(
        "SELECT name,value FROM duckdb_settings() WHERE starts_with(name,'ch_') ORDER BY name",
        "client-settings",
    )
    ddl = "(event_id Int64,customer_id Int32,amount_cents Int64,payload_sha256 String) ENGINE=MergeTree ORDER BY (customer_id,event_id)"
    execute(f"CREATE TABLE default.{table} {ddl}")
    for lo in range(0, rows, batch):
        hi = min(lo + batch, rows)
        profile(
            f"INSERT INTO {hot} SELECT i,CAST(i%{customers} AS INTEGER),i%10000+1,sha256(CAST(i AS VARCHAR)) FROM range({lo},{hi}) t(i);",
            f"insert-{lo}",
        )
        copy(f"SELECT {lo} AS first_id,{hi} AS end_id", f"ack-{lo}")
    copy(maintenance.oracle_query(hot, rows, customers), "hot-rows")
    execute(
        f"INSERT INTO {cold} SELECT event_id,customer_id,amount_cents,payload_sha256 FROM default.{table}",
        "publish",
    )
    copy(f"SELECT {rows} AS acknowledged_rows", "export-ack")
    # The known attached-catalog discovery failure is retained separately. This
    # explicitly labeled path sends ClickHouse SQL through DuckDB's connector.
    scan = (
        "clickhouse_query('ch',"
        + quote(
            "SELECT event_id,customer_id,amount_cents,payload_sha256 FROM "
            + cold
            + f" LIMIT {rows + 1}"
        )
        + ")"
    )
    copy(maintenance.oracle_query(scan, rows, customers), "cold-rows")
    execute(f"CREATE TABLE default.{table}_restored {ddl}")
    execute(
        f"INSERT INTO default.{table}_restored SELECT event_id,customer_id,amount_cents,payload_sha256 FROM {cold}",
        "restore",
    )
    copy(maintenance.oracle_query(restored, rows, customers), "restored-rows")
    parts_query = f"SELECT table,sum(rows) AS rows,sum(bytes_on_disk) AS allocated_part_bytes,sum(data_compressed_bytes) AS compressed_bytes,sum(data_uncompressed_bytes) AS uncompressed_bytes FROM system.parts WHERE active AND database='default' AND table IN ('{table}','{table}_restored') GROUP BY table LIMIT 2"
    copy("SELECT * FROM clickhouse_query('ch', " + quote(parts_query) + ")", "parts")
    sql += [
        "COPY (SELECT extension_name,loaded FROM duckdb_extensions() WHERE loaded ORDER BY extension_name) TO '/results/loaded-extensions.json' (FORMAT JSON,ARRAY true);"
    ]
    return {
        "run.sql": "\n".join(sql) + "\n",
        "run.sh": shell(),
    }


def verify(root, rows):
    if (root / "native-exit.txt").read_text().strip() != "0":
        raise RuntimeError("connector application failed")
    for stage in ("hot", "cold", "restored"):
        maintenance.check_rows(maintenance.single(root, stage + "-rows"), rows)
    if maintenance.single(root, "connector") != {
        "engine": "v2.0.1",
        "extension_version": REVISION,
        "loaded": True,
        "unsigned_allowed": False,
    }:
        raise RuntimeError(
            "application connector engine/revision/signature policy differs"
        )
    loaded = cleanup.load(root / "loaded-extensions.json", root)
    if any(v["extension_name"] in {"iceberg", "httpfs"} for v in loaded):
        raise RuntimeError("application bypassed ClickHouse with a direct S3 extension")
    if maintenance.single(root, "export-ack") != {"acknowledged_rows": rows}:
        raise RuntimeError("missing Iceberg export acknowledgement")
    invocation = cleanup.load(root / "invocation.json", root)
    for lo in range(0, rows, invocation["batch_rows"]):
        if maintenance.single(root, f"ack-{lo}") != {
            "first_id": lo,
            "end_id": min(lo + invocation["batch_rows"], rows),
        }:
            raise RuntimeError("acknowledged input interval differs")
        with (root / f"ack-{lo}.json").open("rb") as witness:
            os.fsync(witness.fileno())
    control.sync_directory(root)
    expected_profiles = {
        "publish",
        "restore",
        "hot-rows",
        "cold-rows",
        "restored-rows",
    } | {f"insert-{lo}" for lo in range(0, rows, invocation["batch_rows"])}
    profiles = {
        name: cleanup.load(root / (name + "-profile.json"), root)["query"]["total_time"]
        for name in expected_profiles
    }
    parts = cleanup.load(root / "parts.json", root)
    if len(parts) != 2 or any(
        v["rows"] != rows or v["compressed_bytes"] <= 0 or v["uncompressed_bytes"] <= 0
        for v in parts
    ):
        raise RuntimeError("hot/restored native storage counters differ")
    return {
        "rows_verified": rows,
        "hot_cold_restored_bodies_correct": True,
        "cold_query_path": "native ClickHouse SQL via DuckDB clickhouse_query",
        "ordinary_attached_cold_path_qualified": False,
        "query_profile_seconds": profiles,
        "native_parts": parts,
        "production_qualified": False,
    }
