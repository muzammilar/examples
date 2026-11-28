"""Account for all roles and distinct volumes, including shared checkpoints/logs."""

import json
import subprocess
import time
import uuid

from lab import ROOT, PROJECT, ROLES, app, command, mysql, owned, save, sql


def disk_usage(path, label):
    volumes, seen = {}, set()
    for role in ROLES:
        container = owned(role)
        for mount in container["Mounts"]:
            if mount["Type"] != "volume" or mount["Name"] in seen:
                continue
            seen.add(mount["Name"])
            owner = subprocess.check_output(
                [
                    "docker",
                    "volume",
                    "inspect",
                    "--format",
                    '{{index .Labels "com.docker.compose.project"}}',
                    mount["Name"],
                ],
                text=True,
            ).strip()
            if owner != PROJECT:
                raise RuntimeError("A mounted data volume is not owned by this example")
            if container["State"]["Running"]:
                raw = command(
                    "exec", "-T", role, "du", "-sk", mount["Destination"]
                ).stdout
            else:
                # Read retained storage with the same image/user while its owner is
                # stopped. No network, writable data mount or host credentials.
                args = [
                    "docker",
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--cpus",
                    ".1",
                    "--memory",
                    "32m",
                    "--label",
                    "com.docker.compose.project=" + PROJECT,
                    "--label",
                    "com.docker.compose.service=disk-observer",
                    "--mount",
                    "type=volume,source=" + mount["Name"] + ",target=/data,readonly",
                    "--mount",
                    "type=bind,source="
                    + str(ROOT / "scripts/finish-tool.sh")
                    + ",target=/finish-tool.sh,readonly",
                ]
                if container["Config"]["User"]:
                    args += ["--user", container["Config"]["User"]]
                args += [
                    "--entrypoint",
                    "sh",
                    container["Config"]["Image"],
                    "/finish-tool.sh",
                    "du",
                    "-sk",
                    "/data",
                ]
                raw = subprocess.check_output(args, text=True)
                (path / ("disk-observer-" + role + "-" + label + ".txt")).write_text(
                    raw
                )
            volumes[mount["Name"]] = {
                "role": role,
                "path": mount["Destination"],
                "allocated_bytes": int(raw.split()[0]) * 1024,
            }
    witness_bytes = sum(
        p.stat().st_size for p in (ROOT / "results/cases").rglob("*.jsonl")
    )
    result = {
        "utc_ns": time.time_ns(),
        "volumes": volumes,
        "allocated_data_bytes": sum(v["allocated_bytes"] for v in volumes.values()),
        "external_journal_logical_bytes": witness_bytes,
        "scope": "Each owned named data volume once, including every physical replica, WAL/binlog, RocksDB and shared checkpoint/log directory. Image layers and generated evidence are separate; host bind-write I/O may not be charged to container block counters.",
    }
    save(path / ("disk-" + label + ".json"), result)
    return result


def resources(path):
    containers, counters = {}, {}
    for role in ROLES:
        row = owned(role)
        containers[role] = {
            "id": row["Id"],
            "image_id": row["Image"],
            "image_reference": row["Config"]["Image"],
            "state": row["State"],
            "nano_cpus": row["HostConfig"]["NanoCpus"],
            "memory_limit_bytes": row["HostConfig"]["Memory"],
        }
        if role == "orders-workload":
            counters[role] = next(
                r["values"]
                for r in app("cgroup", uuid.uuid4().hex, path)
                if r.get("event") == "cgroup"
            )
            continue
        counters[role] = {
            name: command(
                "exec", "-T", role, "cat", "/sys/fs/cgroup/" + name, check=False
            ).stdout.strip()
            for name in ["cpu.stat", "memory.peak", "memory.current", "io.stat"]
        }
    save(path / "containers.json", containers)
    save(
        path / "native-cgroup.json",
        {
            "roles": counters,
            "scope": "Current container incarnation. Restarted roles reset; use phase-aligned observer deltas and pre-exit tool counters as well. Empty/unreadable fields are unavailable, not invented zeroes.",
        },
    )
    disk_usage(path, "settled")
    compression, replicas, durability = {}, {}, {}
    for host in ["ch1", "ch2", "ch3"]:
        compression[host] = sql(
            "SELECT table,sum(rows) AS physical_rows,sum(data_compressed_bytes) AS compressed,sum(data_uncompressed_bytes) AS uncompressed,sum(bytes_on_disk) AS active_part_bytes FROM system.parts WHERE active AND database='pipeline' GROUP BY table",
            host,
        )
        replicas[host] = sql(
            "SELECT table,is_readonly,active_replicas,total_replicas,queue_size,absolute_delay FROM system.replicas WHERE database='pipeline'",
            host,
        )
        durability[host] = {
            "profile": sql(
                "SELECT name,value FROM system.settings WHERE name IN ('insert_quorum','max_threads','insert_quorum_parallel')",
                host,
            ),
            "tables": sql(
                "SELECT name,extract(create_table_query,'fsync_after_insert = ([01])') AS fsync_after_insert,extract(create_table_query,'fsync_part_directory = ([01])') AS fsync_part_directory FROM system.tables WHERE database='pipeline' AND engine LIKE 'Replicated%'",
                host,
            ),
        }
        if len(replicas[host]) != 2 or any(
            r["is_readonly"]
            or int(r["active_replicas"]) != 3
            or int(r["total_replicas"]) != 3
            for r in replicas[host]
        ):
            raise RuntimeError("Incomplete writable target replication on " + host)
        if any(
            r["fsync_after_insert"] != "1" or r["fsync_part_directory"] != "1"
            for r in durability[host]["tables"]
        ):
            raise RuntimeError("Target fsync contract differs on " + host)
    settings = json.loads(
        mysql(
            "SELECT JSON_OBJECT('version',VERSION(),'sync_binlog',@@GLOBAL.sync_binlog,'flush_commit',@@GLOBAL.innodb_flush_log_at_trx_commit,'binlog_format',@@GLOBAL.binlog_format,'binlog_row_image',@@GLOBAL.binlog_row_image,'gtid_mode',@@GLOBAL.gtid_mode,'transaction_isolation',@@GLOBAL.transaction_isolation)"
        )
    )
    if (
        settings["sync_binlog"] != 1
        or settings["flush_commit"] != 1
        or settings["gtid_mode"] != "ON"
        or settings["transaction_isolation"] != "REPEATABLE-READ"
    ):
        raise RuntimeError("Source durability/GTID contract differs from deployment")
    save(path / "compression.json", compression)
    save(path / "replicas.json", replicas)
    save(path / "durability.json", {"source": settings, "targets": durability})
