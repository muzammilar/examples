"""Isolate local S3/catalog ports while the ordinary DuckDB hot workload remains live."""

import ipaddress
import hashlib
import os
import json
import time
import uuid

import duckdb_clickhouse as connector
import duckdb_crash as live
import recovery_control as control
import recovery_cleanup as cleanup

artifacts = live.artifacts


def seed_case(rows):
    identifier = os.environ.get("COLD_SOURCE_CASE")
    if not identifier:
        return None
    case_id = uuid.UUID(identifier)
    if str(case_id) != identifier:
        raise ValueError("canonical source case UUID required")
    case = cleanup.regular(
        cleanup.results_root() / "duckdb-connector-dependencies" / identifier,
        cleanup.results_root(),
    )
    invocation = cleanup.load(case / "invocation.json", case)
    if (
        invocation.get("mode") != "connector-dependencies"
        or invocation.get("rows") != rows
        or invocation.get("table") != "duck_compact_" + case_id.hex
        or list(case.glob("fault-*-intent.json"))
    ):
        raise RuntimeError(
            "only a same-size pre-fault source fixture may be reused read-only"
        )
    result = cleanup.load(case / "cold-reader-000001-result.json", case)
    checked_cold(case, result, rows)
    names = [
        "invocation.json",
        "created-catalog.json",
        "cold-reader-000001-result.json",
        "cold-reader-000001-stdout.json",
    ]
    return {
        "case_id": identifier,
        "table": invocation["table"],
        "table_uuid": cleanup.load(case / "created-catalog.json", case)["metadata"][
            "table-uuid"
        ],
        "artifacts": {
            name: hashlib.sha256((case / name).read_bytes()).hexdigest()
            for name in names
        },
    }


class EndpointFault:
    def __init__(self, root, query, storage):
        self.root, self.query = root, query
        networks = set(query["NetworkSettings"]["Networks"]) & set(
            storage["NetworkSettings"]["Networks"]
        )
        if len(networks) != 1:
            raise RuntimeError("require one shared example network")
        network = networks.pop()
        self.address = str(
            ipaddress.IPv4Address(
                storage["NetworkSettings"]["Networks"][network]["IPAddress"]
            )
        )
        self.chain = "ICE_" + uuid.uuid4().hex[:20]
        self.port, self.identifier = None, None

    def command(self, *args):
        control.owned(self.identifier, "dependency-fault")
        return control.call(
            ["docker", "exec", self.identifier, "iptables", "-w", "5", *args]
        )

    def __enter__(self):
        name = "iceberg-dependency-" + uuid.uuid4().hex
        env = control.DOCKER_ENV | {
            "RESULTS_DIR": str(self.root.relative_to(control.ROOT))
        }
        self.identifier = control.call(
            control.COMPOSE
            + ["run", "-d", "--no-deps", "--name", name, "dependency-fault"],
            env=env,
        ).strip()
        value = control.owned(self.identifier, "dependency-fault")
        control.save(self.root / "network-helper-created.json", control.state(value))
        config = value["HostConfig"]
        if (
            value["Name"] != "/" + name
            or config["NetworkMode"] != "container:" + self.query["Id"]
            or {cap.removeprefix("CAP_") for cap in config["CapAdd"] or []}
            != {"NET_ADMIN"}
            or set(config["CapDrop"] or []) != {"ALL"}
            or config["Privileged"]
            or not config["ReadonlyRootfs"]
            or config["Binds"]
            or config["Memory"] != 67108864
            or config["NanoCpus"] != 250000000
        ):
            control.call(["docker", "stop", "--time", "5", self.identifier])
            rejected = control.owned(self.identifier, "dependency-fault")
            control.save(
                self.root / "network-helper-rejected.json", control.state(rejected)
            )
            raise RuntimeError("network helper namespace/security/budget differs")
        image = json.loads(
            control.call(["docker", "image", "inspect", value["Image"]])
        )[0]
        if image["Config"].get("Labels", {}).get("org.examples.owner") != control.OWNER:
            raise RuntimeError("network helper image lacks ownership")
        control.save(
            self.root / "network-helper.json",
            control.state(value)
            | {
                "network_namespace": config["NetworkMode"],
                "target_address": self.address,
            },
        )
        control.save_text(
            self.root / "iptables-version.txt",
            control.call(["docker", "exec", self.identifier, "iptables", "--version"]),
        )
        self.baseline = self.command("-S")
        if self.chain in self.baseline:
            raise RuntimeError("new fault chain already exists")
        control.save_text(self.root / "iptables-before.txt", self.baseline)
        return self

    def apply(self, port):
        if port not in (8333, 8181) or self.port is not None:
            raise RuntimeError("one local dependency fault at a time")
        self.port = port
        control.save(
            self.root / f"fault-{port}-intent.json",
            {
                "port": port,
                "address": self.address,
                "chain": self.chain,
                "started_ns": time.time_ns(),
            },
        )
        self.command("-N", self.chain)
        self.command(
            "-A", self.chain, "-p", "tcp", "-j", "REJECT", "--reject-with", "tcp-reset"
        )
        self.command(
            "-I",
            "OUTPUT",
            "1",
            "-d",
            self.address,
            "-p",
            "tcp",
            "--dport",
            str(port),
            "-j",
            self.chain,
        )
        self.command(
            "-C",
            "OUTPUT",
            "-d",
            self.address,
            "-p",
            "tcp",
            "--dport",
            str(port),
            "-j",
            self.chain,
        )
        control.save_text(self.root / f"fault-{port}-rules.txt", self.command("-S"))

    def prove(self):
        output = self.command("-L", self.chain, "-n", "-v", "-x")
        control.save_text(self.root / f"fault-{self.port}-counters.txt", output)
        rules = [line.split() for line in output.splitlines() if "REJECT" in line]
        if len(rules) != 1 or int(rules[0][0]) < 1:
            raise RuntimeError("dependency rejection had no observed packets")

    def clear(self):
        if self.port is None:
            return
        # Inspect successfully before deciding which exact owned rules exist.
        rules = self.command("-S")
        if any(
            line.startswith("-A OUTPUT ") and line.endswith("-j " + self.chain)
            for line in rules.splitlines()
        ):
            self.command(
                "-D",
                "OUTPUT",
                "-d",
                self.address,
                "-p",
                "tcp",
                "--dport",
                str(self.port),
                "-j",
                self.chain,
            )
        if "-N " + self.chain in rules.splitlines():
            control.save_text(
                self.root / f"fault-{self.port}-cleanup-counters.txt",
                self.command("-L", self.chain, "-n", "-v", "-x"),
            )
            self.command("-F", self.chain)
            self.command("-X", self.chain)
        after = self.command("-S")
        control.save_text(self.root / f"fault-{self.port}-healed.txt", after)
        if after != self.baseline:
            raise RuntimeError(
                "namespace rules did not return to the original baseline"
            )
        self.port = None

    def __exit__(self, *_):
        self.clear()
        control.call(["docker", "stop", "--time", "5", self.identifier])
        value = control.owned(self.identifier, "dependency-fault")
        control.save(self.root / "network-helper-stopped.json", control.state(value))
        if value["State"]["Running"] or value["State"]["Pid"]:
            raise RuntimeError("network helper is not fenced")
        control.call(["docker", "rm", self.identifier])


def catalog(storage, table):
    _, auth = connector.catalog_request(
        storage,
        "oauth/tokens",
        body="grant_type=client_credentials&client_id=demo&client_secret=demo",
    )
    _, value = connector.catalog_request(
        storage, "namespaces/lab/tables/" + table, token=auth["access_token"]
    )
    return value


def cold_oracle(table, rows, append_first, appended):
    invalid = f"event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR NOT (event_id>=0 AND event_id<{rows} OR event_id>={append_first} AND event_id<{append_first + appended}) OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=lower(hex(SHA256(toString(event_id))))"
    return f"SELECT count() AS rows,uniqExact(event_id) AS unique_ids,countIf({invalid}) AS invalid_bodies FROM lake.`lab.{table}` LIMIT 1 SETTINGS use_iceberg_metadata_files_cache=0,iceberg_metadata_staleness_ms=0,enable_filesystem_cache=0,max_execution_time=15,max_memory_usage=805306368,max_rows_to_read=30000000,max_bytes_to_read=4294967296,timeout_before_checking_execution_speed=0"


def checked_cold(root, result, count):
    stem = f"cold-reader-{result['sequence']:06d}"
    if result["status"] != "ok":
        raise RuntimeError("cold query did not recover")
    body = json.loads((root / (stem + "-stdout.json")).read_text())
    if len(body) != 1 or {k: int(v) for k, v in body[0].items()} != {
        "rows": count,
        "unique_ids": count,
        "invalid_bodies": 0,
    }:
        raise RuntimeError("cold complete-body/range reconciliation failed")


def drive(root, table, rows, identifier, query, sampler):
    storage = control.service("storage")
    source = seed_case(rows)
    source_table = source["table"] if source else table + "_source"
    connector.setup(root, table, storage)
    if source is None:
        (root / "source-fixture").mkdir()
        connector.setup(root / "source-fixture", source_table, storage)
    control.save(
        root / "cold-source.json", source or {"table": source_table, "fresh": True}
    )
    source_before = catalog(storage, source_table)
    if source and source_before["metadata"]["table-uuid"] != source["table_uuid"]:
        raise RuntimeError("read-only source table UUID changed")
    control.save(root / "source-catalog-before.json", source_before)
    sequence = [0]

    def execute(sql):
        return "CALL clickhouse_execute('ch','" + sql.replace("'", "''") + "');"

    def read(sql):
        return "SELECT * FROM clickhouse_query('ch','" + sql.replace("'", "''") + "');"

    def operation(request, transition, progress):
        def archive(sql):
            value = request("archive", sequence[0], execute(sql))
            sequence[0] += 1
            return value

        def cold(count, first, appended, *, target=table, seed_rows=0):
            value = request(
                "cold-reader",
                sequence[0],
                read(cold_oracle(target, seed_rows, first, appended)),
            )
            sequence[0] += 1
            checked_cold(root, value, count)
            return value

        hot = f"default.{table}"
        cold_table = f"lake.`lab.{table}`"
        if source is None:
            initial = archive(
                f"INSERT INTO lake.`lab.{source_table}` SELECT event_id,customer_id,amount_cents,payload_sha256 FROM {hot} WHERE event_id<{rows}"
            )
            if initial["status"] != "ok":
                raise RuntimeError("initial cold source publish not acknowledged")
        writers = [json.loads(p.read_text()) for p in root.glob("writer-*-result.json")]
        first = min(v["first"] for v in writers if v["status"] == "ok")
        control.save(
            root / "cold-input.json",
            {
                "seed_rows": rows,
                "append_first": first,
                "append_rows_per_recovery": 1000,
            },
        )
        cold(rows, 0, 0, target=source_table, seed_rows=rows)
        source_snapshot = catalog(storage, source_table)
        control.save(root / "source-catalog-published.json", source_snapshot)
        outcomes = []
        with EndpointFault(root, query, storage) as fault:
            sampler.bind("dependency-fault", fault.identifier, "/run")
            for index, (label, port) in enumerate((("s3", 8333), ("catalog", 8181))):
                appended = index * 1000
                before = catalog(storage, table)
                control.save(root / (label + "-catalog-before.json"), before)
                transition(label + "-outage")
                try:
                    fault.apply(port)
                    failed_read = request(
                        "cold-reader",
                        sequence[0],
                        read(cold_oracle(source_table, rows, 0, 0)),
                    )
                    sequence[0] += 1
                    if failed_read["status"] == "ok":
                        if port == 8333:
                            raise RuntimeError(
                                "cold read did not observe S3 rejection with data cache disabled"
                            )
                        checked_cold(root, failed_read, rows)
                    statement = f"INSERT INTO {cold_table} SELECT event_id,customer_id,amount_cents,payload_sha256 FROM {hot} WHERE event_id>={first + appended} AND event_id<{first + appended + 1000}"
                    attempted = archive(statement)
                    if attempted["status"] == "ok":
                        raise RuntimeError(
                            "archive unexpectedly acknowledged under dependency loss"
                        )
                    progress(label + "-outage")
                    fault.prove()
                    client = [
                        "docker",
                        "exec",
                        query["Id"],
                        "clickhouse-client",
                        "--password",
                        "local-reader",
                        "--query",
                    ]
                    active = f"SELECT count() FROM system.processes WHERE startsWith(query,'INSERT INTO lake.') AND position(query,'{table}')>0 LIMIT 1 SETTINGS max_execution_time=10 FORMAT TSV"
                    control.save(
                        root / (label + "-server-before-heal.json"),
                        {
                            "active_archives": int(
                                control.call(client + [active]).strip()
                            )
                        },
                    )
                finally:
                    fault.clear()
                transition(label + "-recovery")
                deadline = time.monotonic() + 120
                while control.call(client + [active]).strip() != "0":
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "archive remains active after healing; do not retry"
                        )
                    time.sleep(0.2)
                after = catalog(storage, table)
                control.save(root / (label + "-catalog-after.json"), after)
                changed = after["metadata"].get("current-snapshot-id", -1) != before[
                    "metadata"
                ].get("current-snapshot-id", -1)
                cold(rows, 0, 0, target=source_table, seed_rows=rows)
                if changed:
                    # A deadline/closed client did not undo the server commit.
                    # Accept only the complete intended batch, and never replay it.
                    cold(appended + 1000, first, appended + 1000)
                    outcomes.append(
                        {
                            "dependency": label,
                            "ambiguous_applied_rows": 1000,
                            "recovery_acknowledged_rows": 0,
                            "retried": False,
                        }
                    )
                else:
                    if appended:
                        cold(appended, first, appended)
                    elif after["metadata"].get("current-snapshot-id", -1) not in (
                        -1,
                        None,
                    ) or after["metadata"].get("snapshots"):
                        raise RuntimeError(
                            "new write-probe table has a published snapshot"
                        )
                    # Fenced client, quiescent server, stable catalog and complete
                    # logical absence permit this serial single-archiver replay.
                    recovered = archive(statement)
                    if recovered["status"] != "ok":
                        raise RuntimeError("recovery archive not acknowledged")
                    cold(appended + 1000, first, appended + 1000)
                    outcomes.append(
                        {
                            "dependency": label,
                            "ambiguous_applied_rows": 0,
                            "recovery_acknowledged_rows": 1000,
                            "retried": True,
                        }
                    )
                progress(label + "-recovery")
        sampler.unbind("dependency-fault")
        healthy = archive(
            f"INSERT INTO {cold_table} SELECT event_id,customer_id,amount_cents,payload_sha256 FROM {hot} WHERE event_id>={first + 2000} AND event_id<{first + 3000}"
        )
        if healthy["status"] != "ok":
            raise RuntimeError(
                "fresh healthy archive did not acknowledge after both outages"
            )
        cold(3000, first, 3000)
        source_after = catalog(storage, source_table)
        control.save(root / "source-catalog-after.json", source_after)
        probe_after = catalog(storage, table)
        control.save(root / "probe-catalog-final.json", probe_after)
        audit_sql = [
            "SET extension_directories=['/opt/duckdb/extensions']; LOAD httpfs;",
            "CREATE SECRET audit_s3 (TYPE S3,KEY_ID 'demo',SECRET 'demo',ENDPOINT 'storage:8333',URL_STYLE 'path',USE_SSL false);",
        ]
        for label, metadata in (("source", source_after), ("probe", probe_after)):
            location = metadata["metadata"]["location"]
            if not location.startswith("s3://warehouse/lab/duck_compact_") or any(
                c in location for c in ("'", "\\", "*", "?", "..")
            ):
                raise RuntimeError("metadata footer audit escaped owned S3 prefix")
            pattern = location + "/**/*.parquet"
            audit_sql += [
                f"COPY (SELECT count(DISTINCT file_name) AS files,count(*) AS column_chunks,sum(total_compressed_size) AS compressed_bytes,sum(total_uncompressed_size) AS uncompressed_bytes FROM parquet_metadata('{pattern}')) TO '/results/{label}-compression.json' (FORMAT JSON,ARRAY true);",
                f"COPY (SELECT count(*) AS files,sum(num_rows) AS physical_rows,sum(file_size_bytes) AS file_bytes FROM parquet_file_metadata('{pattern}')) TO '/results/{label}-parquet.json' (FORMAT JSON,ARRAY true);",
            ]
        audit = request("storage-audit", sequence[0], "\n".join(audit_sql))
        if audit["status"] != "ok":
            raise RuntimeError("independent physical Parquet footer audit failed")
        for label in ("source", "probe"):
            body = json.loads((root / (label + "-compression.json")).read_text())
            if (
                len(body) != 1
                or int(body[0]["files"]) < 1
                or int(body[0]["compressed_bytes"]) <= 0
                or int(body[0]["uncompressed_bytes"]) <= 0
            ):
                raise RuntimeError("missing encoded compression measurements")
        if source_after["metadata"].get("current-snapshot-id") != source_snapshot[
            "metadata"
        ].get("current-snapshot-id"):
            raise RuntimeError("read-only source snapshot changed")
        control.save(
            root / "dependency-checks.json",
            {
                "cold_rows_verified": rows,
                "recovery_probe_rows_verified": 3000,
                "healthy_archive_acknowledged_rows": 1000,
                "ambiguous_archive_outcomes": outcomes,
                "source_snapshot_unchanged": True,
                "dependency_ports_tested": [8333, 8181],
                "source_deleted": False,
                "retry_preconditions": [
                    "no active archive",
                    "unchanged catalog snapshot",
                    "complete cold readback",
                ],
            },
        )
        transition("after")
        progress("after", minimum=4)

    return live.drive(
        root, table, rows, identifier, query, sampler, operation=operation
    )


def verify(root, table, rows, query):
    checks = live.verify(root, table, rows, query)
    checks.update(json.loads((root / "dependency-checks.json").read_text()))
    checks["scope"] = (
        "Independent endpoint-specific TCP rejects in owned ClickHouse namespace; live ordinary DuckDB hot workload and explicit native pass-through cold diagnostics. No storage process crash, host network fault, power loss or complete architecture qualification."
    )
    return checks
