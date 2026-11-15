"""A distinct S3 prefix and local metadata/cache per replica; no shared-disk writes."""

from pathlib import Path

root = Path("config/generated")
root.mkdir(parents=True, exist_ok=True)
for i in range(3):
    keeper = (
        "<clickhouse><logger><level>warning</level></logger><listen_host>0.0.0.0</listen_host><keeper_server><tcp_port>9181</tcp_port><server_id>"
        + str(i + 1)
        + "</server_id><log_storage_path>/var/lib/clickhouse-keeper/coordination/log</log_storage_path><snapshot_storage_path>/var/lib/clickhouse-keeper/coordination/snapshots</snapshot_storage_path><coordination_settings><operation_timeout_ms>10000</operation_timeout_ms><session_timeout_ms>30000</session_timeout_ms><raft_logs_level>warning</raft_logs_level></coordination_settings><raft_configuration>"
        + "".join(
            f"<server><id>{j + 1}</id><hostname>keeper{j}</hostname><port>9234</port></server>"
            for j in range(3)
        )
        + "</raft_configuration></keeper_server></clickhouse>"
    )
    (root / f"keeper{i}.xml").write_text(keeper + "\n")
    cfg = f"""<clickhouse>
  <logger><level>warning</level></logger>
  <max_server_memory_usage>1800000000</max_server_memory_usage>
  <mark_cache_size>67108864</mark_cache_size>
  <uncompressed_cache_size>0</uncompressed_cache_size>
  <interserver_http_credentials><user>replication</user><password>local-only-interserver</password></interserver_http_credentials>
  <macros><shard>01</shard><replica>replica{i}</replica></macros>
  <zookeeper>{"".join(f"<node><host>keeper{j}</host><port>9181</port></node>" for j in range(3))}</zookeeper>
  <remote_servers><lab><shard><internal_replication>true</internal_replication>{"".join(f"<replica><host>ch{j}</host><port>9000</port><user>default</user><password>local-only-clickhouse</password></replica>" for j in range(3))}</shard></lab></remote_servers>
  <storage_configuration>
    <disks>
      <remote><type>s3</type><endpoint>http://s3:9000/lab-objects/clickhouse/replica{i}/</endpoint><access_key_id>GK0123456789abcdef01234567</access_key_id><secret_access_key>local-only-object-storage-secret-key</secret_access_key><region>us-east-1</region><metadata_path>/var/lib/clickhouse/disks/remote/</metadata_path><support_batch_delete>false</support_batch_delete></remote>
      <remote_cache><type>cache</type><disk>remote</disk><path>/var/lib/clickhouse/s3-cache/</path><max_size>536870912</max_size><cache_on_write_operations>false</cache_on_write_operations></remote_cache>
    </disks>
    <policies><tiered><volumes><hot><disk>default</disk></hot><cold><disk>remote_cache</disk><perform_ttl_move_on_insert>false</perform_ttl_move_on_insert></cold></volumes><move_factor>0</move_factor></tiered></policies>
  </storage_configuration>
</clickhouse>
"""
    (root / f"ch{i}.xml").write_text(cfg)
