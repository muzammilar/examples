"""Validate Spark output through the independent Arrow Parquet reader."""
import json
import hashlib
import resource
import time
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq


def main():
    started = time.monotonic()
    root = Path("/data/orders")
    if not (root / "_SUCCESS").exists():
        raise ValueError("Spark output lacks its success marker")
    files = sorted(root.glob("part-*.parquet"))
    seen = bytearray(1000000)
    count = total = compressed = uncompressed = 0
    codecs = set()
    for path in files:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if set(schema.names) != {"order_id", "customer_id", "amount_cents", "region"} or len(schema) != 4:
            raise ValueError(f"Wrong columns: {schema}")
        if any(field.type != pa.int64() for field in schema):
            raise ValueError(f"Wrong column types: {schema}")
        for group_id in range(parquet.metadata.num_row_groups):
            group = parquet.metadata.row_group(group_id)
            for column_id in range(group.num_columns):
                column = group.column(column_id)
                compressed += column.total_compressed_size
                uncompressed += column.total_uncompressed_size
                codecs.add(column.compression)
        for batch in parquet.iter_batches(batch_size=65536):
            rows = batch.to_pydict()
            for order, customer, amount, region in zip(
                rows["order_id"], rows["customer_id"],
                rows["amount_cents"], rows["region"], strict=True,
            ):
                if order is None or not 0 <= order < len(seen) or seen[order]:
                    raise ValueError(f"Invalid or duplicate order {order}")
                if customer != order % 10000 or amount != 100 + order % 1000 or region != customer % 10:
                    raise ValueError(f"Wrong order body {order}")
                seen[order] = 1
                count += 1
                total += amount
    if count != len(seen) or total != 599500000:
        raise ValueError(f"Incomplete output {count} rows, {total} cents")
    manifest = []
    for path in files:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest.append({"file": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    usage = resource.getrusage(resource.RUSAGE_SELF)
    container = {}
    for name in ("cpu.stat", "memory.peak", "io.stat"):
        try:
            container[name] = (Path("/sys/fs/cgroup") / name).read_text()
        except OSError as error:
            container[name] = {"unavailable": str(error)}
    print(json.dumps({
        "verifier_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "read_hash_verify_ms": (time.monotonic() - started) * 1000,
        "process_cpu_user_s": usage.ru_utime,
        "process_cpu_system_s": usage.ru_stime,
        "process_peak_rss_bytes_linux": usage.ru_maxrss * 1024,
        "container_counters_before_exit": container,
        "orders": count, "amount_cents": total, "missing": 0, "wrong": 0,
        "file_manifest": manifest,
        "parquet_files": len(files), "codecs": sorted(codecs),
        "column_compressed_bytes": compressed,
        "column_uncompressed_bytes": uncompressed,
        "column_compression_ratio": uncompressed / compressed,
        "parquet_file_bytes": sum(p.stat().st_size for p in files),
        "allocated_output_bytes": sum(p.stat().st_blocks * 512 for p in root.rglob("*") if p.is_file()),
    }))


if __name__ == "__main__":
    main()
