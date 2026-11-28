"""Fresh native HDFS download; independent Arrow body and compression oracle."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pyarrow.parquet as pq
from publication import check_manifest


def main():
    batch = sys.argv[1]
    if batch not in ("baseline", "executor", "datanode"):
        raise ValueError("Invalid batch")
    rows, customers = int(os.environ["ORDER_COUNT"]), int(os.environ["CUSTOMER_COUNT"])
    subprocess.run(
        ["hdfs", "dfs", "-get", "/lab/published/" + batch, "/tmp/published"], check=True
    )
    root = Path("/tmp/published")
    manifest = json.loads((root / "_MANIFEST.json").read_text())
    actual = {
        str(p.relative_to(root)): p.stat().st_size for p in root.rglob("*.parquet")
    }
    check_manifest(manifest, actual, batch, rows, customers)
    seen = bytearray(rows + 1)
    compressed = uncompressed = file_bytes = 0
    codecs, files, totals = set(), [], {}
    shards = set()
    started, cpu = time.monotonic(), time.process_time()
    for path in sorted(root.rglob("*.parquet")):
        metadata = pq.read_metadata(path)
        file_bytes += path.stat().st_size
        for i in range(metadata.num_row_groups):
            for j in range(metadata.num_columns):
                column = metadata.row_group(i).column(j)
                compressed += column.total_compressed_size
                uncompressed += column.total_uncompressed_size
                codecs.add(column.compression)
        files.append(
            dict(
                path=str(path.relative_to(root)),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,
            )
        )
        if path.parent.name.startswith("customer_shard="):
            shard = int(path.parent.name.split("=")[1])
            shards.add(shard)
            for chunk in pq.ParquetFile(path).iter_batches(batch_size=4096):
                for row in chunk.to_pylist():
                    key = row["order_id"]
                    if not isinstance(key, int) or not 1 <= key <= rows or seen[key]:
                        raise ValueError("Duplicate/invalid order")
                    customer = 1 + (key - 1) % customers
                    expected = dict(
                        order_id=key,
                        customer_id=customer,
                        amount_cents=100 + key % 1000,
                        region="region-" + str(customer % 16),
                        order_revision=1,
                        customer_revision=1,
                        event_second=1700000000 + key % 86400,
                    )
                    if row != expected or shard != customer % 3:
                        raise ValueError("Wrong body or customer partition")
                    seen[key] = 1
                    n, amount = totals.get(expected["region"], (0, 0))
                    totals[expected["region"]] = (
                        n + 1,
                        amount + expected["amount_cents"],
                    )
    if shards != {customer % 3 for customer in range(1, min(customers, 3) + 1)}:
        raise ValueError("Missing/wrong customer partition coverage")
    if sum(seen) != rows:
        raise ValueError("Missing orders")
    aggregates = {}
    for path in (root / "regions").glob("*.parquet"):
        for row in pq.ParquetFile(path).read().to_pylist():
            if row["region"] in aggregates:
                raise ValueError("Duplicate region")
            aggregates[row["region"]] = row["orders"], row["amount_cents"]
    if aggregates != totals:
        raise ValueError("Wrong regional aggregates")
    print(
        json.dumps(
            dict(
                passed=True,
                batch=batch,
                rows=rows,
                customer_partitions=sorted(shards),
                files=files,
                parquet_column_compressed_bytes=compressed,
                parquet_column_uncompressed_bytes=uncompressed,
                parquet_file_bytes=file_bytes,
                codecs=sorted(codecs),
                compression_ratio=uncompressed / compressed,
                oracle_cpu_s=time.process_time() - cpu,
                oracle_wall_s=time.monotonic() - started,
            )
        )
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        print(
            json.dumps(
                {
                    "actor_counters": {
                        n: (Path("/sys/fs/cgroup") / n).read_text()
                        for n in ("cpu.stat", "memory.peak", "io.stat")
                    }
                }
            )
        )
