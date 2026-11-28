"""Effective topology, durability, compression and volume accounting."""

import json
from lab import ROLES, owned, save, sql, command, pg


def resources(path):
    rows = {}
    for role in ROLES + ["orders-workload"]:
        row = owned(role)
        rows[role] = {
            "id": row["Id"],
            "image": row["Image"],
            "limits": {
                "cpu": row["HostConfig"]["NanoCpus"],
                "memory": row["HostConfig"]["Memory"],
            },
            "state": row["State"],
        }
    save(path / "containers.json", rows)
    save(
        path / "compression.json",
        {
            host: sql(
                "SELECT table,sum(rows) AS rows,sum(data_compressed_bytes) AS compressed,sum(data_uncompressed_bytes) AS uncompressed,sum(bytes_on_disk) AS disk FROM system.parts WHERE active AND database='pipeline' GROUP BY table",
                host,
            )
            for host in ["ch1", "ch2", "ch3"]
        },
    )
    replicas = {
        host: sql(
            "SELECT table,is_readonly,active_replicas,total_replicas,queue_size,absolute_delay FROM system.replicas WHERE database='pipeline'",
            host,
        )
        for host in ["ch1", "ch2", "ch3"]
    }
    save(path / "replicas.json", replicas)
    if any(
        not rows
        or any(
            r["is_readonly"] or r["active_replicas"] != 3 or r["total_replicas"] != 3
            for r in rows
        )
        for rows in replicas.values()
    ):
        raise RuntimeError("Read-only or incomplete replica membership")
    counters = {}
    for role in ROLES:
        counters[role] = {
            name: command(
                "exec", "-T", role, "cat", "/sys/fs/cgroup/" + name, check=False
            ).stdout.strip()
            for name in ["cpu.stat", "memory.peak", "memory.current", "io.stat"]
        }
    save(
        path / "native-cgroup.json",
        {
            "scope": "Current-container-incarnation cumulative CPU/I/O and native memory high-water. Unrestarted roles include setup/import; restarted Connect does not retain its pre-crash cgroup totals. Empty values are unavailable, not zero.",
            "roles": counters,
        },
    )
    disk = {}
    for role in ROLES:
        if role == "connect":
            continue  # Connect offsets are in the replicated Kafka internal topic.
        mount = (
            "/var/lib/postgresql"
            if role == "postgres"
            else (
                "/var/lib/kafka/data"
                if role.startswith(("broker", "controller"))
                else "/var/lib/clickhouse"
            )
        )
        raw = command("exec", "-T", role, "du", "-sk", mount).stdout
        disk[role] = {"allocated_bytes": int(raw.split()[0]) * 1024, "mount": mount}
    save(
        path / "disk-space.json",
        {
            "roles": disk,
            "physical_copies_total_bytes": sum(
                row["allocated_bytes"] for row in disk.values()
            ),
            "scope": "Allocated data-volume blocks; includes WAL/logs/indexes and all replicas, not image layers or device write amplification.",
        },
    )
    save(
        path / "source-settings.json",
        json.loads(
            pg(
                "SELECT json_build_object('version',version(),'wal_level',current_setting('wal_level'),'synchronous_commit',current_setting('synchronous_commit'),'fsync',current_setting('fsync'),'full_page_writes',current_setting('full_page_writes'))"
            )
        ),
    )
    save(
        path / "clickhouse-settings.json",
        {
            host: {
                "version": sql("SELECT version() AS version", host),
                "durability": sql(
                    "SELECT name,value FROM system.settings WHERE name IN ('insert_quorum','insert_quorum_parallel','max_threads')",
                    host,
                ),
                "table_durability": sql(
                    "SELECT name,extract(create_table_query,'fsync_after_insert = ([01])') AS fsync_after_insert,extract(create_table_query,'fsync_part_directory = ([01])') AS fsync_part_directory FROM system.tables WHERE database='pipeline' AND engine LIKE 'Replicated%'",
                    host,
                ),
            }
            for host in ["ch1", "ch2", "ch3"]
        },
    )

    command(
        "exec",
        "-T",
        "ch1",
        "clickhouse-client",
        "--user",
        "lab",
        "--password",
        "local-control-only",
        "--query",
        "SYSTEM SYNC REPLICA pipeline.orders_state",
    )
