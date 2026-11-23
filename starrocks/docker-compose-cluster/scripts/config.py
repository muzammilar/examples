"""Generate the pinned native FE/BE topology and owned persistent disks."""

import json
import os
from pathlib import Path

project = os.environ.get("COMPOSE_PROJECT_NAME", "starrocks-orders")
generated = Path("config/generated")
generated.mkdir(parents=True, exist_ok=True)
services = {}
volumes = {}
for role in ("fe", "be"):
    for i in range(3):
        name = f"sr-{role}-{i}"
        volumes[name] = {}
        services[name] = {
            "image": f"{project}-{role}:dev",
            "build": {"context": "./.build/docker", "dockerfile": f"Dockerfile.{role}"},
            "hostname": name,
            "cpus": 0.5 if role == "fe" else 1,
            "mem_limit": "1280m" if role == "fe" else "2g",
            "volumes": [f"{name}:/data"],
            "healthcheck": {
                "test": [
                    "CMD",
                    "mysql",
                    "-h",
                    "127.0.0.1",
                    "-P",
                    "9030",
                    "-u",
                    "root",
                    "-N",
                    "-e",
                    "SELECT 1",
                ]
                if role == "fe"
                else ["CMD", "curl", "-fsS", "http://127.0.0.1:8040/api/health"],
                "interval": "5s",
                "timeout": "5s",
                "retries": 90,
                "start_period": "30s",
            },
        }
        if role == "be" or i != 0:
            services[name]["depends_on"] = {
                "sr-fe-0": {
                    "condition": "service_healthy"
                    if role == "be"
                    else "service_started"
                }
            }
keeper_nodes = "".join(
    f"<node><host>keeper{i}</host><port>9181</port></node>" for i in range(3)
)
remote_shards = "".join(
    "<shard><internal_replication>true</internal_replication>"
    + "".join(
        f"<replica><host>ch{s}{r}</host><port>9000</port><user>default</user><password>local-only-clickhouse</password></replica>"
        for r in range(3)
    )
    + "</shard>"
    for s in range(3)
)
for i in range(3):
    name = f"keeper{i}"
    raft = "".join(
        f"<server><id>{j + 1}</id><hostname>keeper{j}</hostname><port>9234</port></server>"
        for j in range(3)
    )
    (generated / (name + ".xml")).write_text(
        f"<clickhouse><logger><level>warning</level></logger><listen_host>0.0.0.0</listen_host><keeper_server><tcp_port>9181</tcp_port><server_id>{i + 1}</server_id><log_storage_path>/var/lib/clickhouse-keeper/coordination/log</log_storage_path><snapshot_storage_path>/var/lib/clickhouse-keeper/coordination/snapshots</snapshot_storage_path><coordination_settings><operation_timeout_ms>30000</operation_timeout_ms><session_timeout_ms>30000</session_timeout_ms><raft_logs_level>warning</raft_logs_level></coordination_settings><raft_configuration>{raft}</raft_configuration></keeper_server></clickhouse>\n"
    )
    volumes[name] = {}
    services[name] = {
        "image": "clickhouse/clickhouse-keeper:26.10.2.103",
        "hostname": name,
        "cpus": 0.3,
        "mem_limit": "256m",
        "volumes": [
            f"./config/generated/{name}.xml:/etc/clickhouse-keeper/keeper_config.xml:ro",
            f"{name}:/var/lib/clickhouse-keeper",
        ],
        "healthcheck": {
            "test": [
                "CMD",
                "clickhouse-keeper-client",
                "--host",
                "127.0.0.1",
                "--port",
                "9181",
                "--query",
                "ruok",
            ],
            "interval": "5s",
            "timeout": "5s",
            "retries": 90,
            "start_period": "15s",
        },
    }
for shard in range(3):
    for replica in range(3):
        name = f"ch{shard}{replica}"
        (generated / (name + ".xml")).write_text(f"""<clickhouse>
<logger><level>warning</level></logger>
<trace_log remove="1"/><metric_log remove="1"/><asynchronous_metric_log remove="1"/>
<query_metric_log remove="1"/><processors_profile_log remove="1"/><query_thread_log remove="1"/>
<opentelemetry_span_log remove="1"/><query_views_log remove="1"/>
<query_log><reserved_size_rows>64</reserved_size_rows><max_size_rows>4096</max_size_rows><flush_interval_milliseconds>1000</flush_interval_milliseconds></query_log>
<part_log><reserved_size_rows>64</reserved_size_rows><max_size_rows>4096</max_size_rows><flush_interval_milliseconds>1000</flush_interval_milliseconds></part_log>
<text_log><level>warning</level><reserved_size_rows>64</reserved_size_rows><max_size_rows>4096</max_size_rows></text_log>
<max_server_memory_usage>800000000</max_server_memory_usage>
<mark_cache_size>33554432</mark_cache_size><uncompressed_cache_size>0</uncompressed_cache_size>
<macros><shard>{shard}</shard><replica>{replica}</replica></macros>
<interserver_http_credentials><user>replication</user><password>local-only-interserver</password></interserver_http_credentials>
<zookeeper>{keeper_nodes}</zookeeper>
<remote_servers><orders_cluster>{remote_shards}</orders_cluster></remote_servers>
</clickhouse>\n""")
        volumes[name] = {}
        services[name] = {
            "image": "clickhouse/clickhouse-server:26.10.2.103",
            "hostname": name,
            "cpus": 0.4,
            "mem_limit": "1g",
            "environment": {
                "CLICKHOUSE_PASSWORD": "local-only-clickhouse",
                "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT": "1",
            },
            "volumes": [
                f"./config/generated/{name}.xml:/etc/clickhouse-server/config.d/lab.xml:ro",
                f"{name}:/var/lib/clickhouse",
            ],
            "healthcheck": {
                "test": [
                    "CMD",
                    "clickhouse-client",
                    "--password",
                    "local-only-clickhouse",
                    "--query",
                    "SELECT 1",
                ],
                "interval": "5s",
                "timeout": "5s",
                "retries": 90,
                "start_period": "30s",
            },
        }
Path("results").mkdir(exist_ok=True)
services["client"] = {
    "image": project + "-client:dev",
    "build": {"context": "./.build/mutable-orders"},
    "profiles": ["tools"],
    "cpus": 2,
    "mem_limit": "2g",
    "environment": {
        "COUNT": os.environ.get("COUNT", "1000000"),
        "FAULT_BATCH_ROWS": os.environ.get("FAULT_BATCH_ROWS", "1000"),
        "FAULT_PAUSE_MS": os.environ.get("FAULT_PAUSE_MS", "1000"),
    },
    "volumes": ["./results:/results"],
}
services["observer"] = {
    **services["client"],
    "cpus": 0.5,
    "mem_limit": "256m",
}
Path("docker-compose.json").write_text(
    json.dumps({"name": project, "services": services, "volumes": volumes}, indent=2)
    + "\n"
)
