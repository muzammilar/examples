"""Independent Iceberg/Parquet and Kafka-ACK oracle; no Flink/Spark query reuse."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import time
from urllib.parse import quote
from urllib.request import urlopen

import pyarrow.parquet as pq
from pyiceberg.table import StaticTable
from rows import witness, check_row

FIELDS = (
    "phase",
    "order_id",
    "customer_id",
    "amount_cents",
    "region",
    "order_revision",
    "customer_revision",
    "event_second",
    "source_partition",
    "source_offset",
    "customer_shard",
)


def location(value):
    if not value.startswith("s3://warehouse/") or ".." in value.split("/"):
        raise ValueError("Non-lab object location")
    return value


def load(uri):
    props = json.loads(Path("/app/catalog.json").read_text())
    if props["s3.endpoint"] != "http://s3:8333":
        raise ValueError("Non-lab endpoint")
    # Do not pass Java's io-impl class to Python or discover host credentials.
    selected = {k: v for k, v in props.items() if k.startswith("s3.")}
    selected["s3.region"] = props["client.region"]
    selected["py-io-impl"] = "pyiceberg.io.pyarrow.PyArrowFileIO"
    return StaticTable.from_metadata(location(uri), properties=selected)


def body(table, uri):
    with table.io.new_input(location(uri)).open() as stream:
        return stream.read()


def get(url):
    # URLs are constructed from the fixed private service names below.
    with urlopen(url, timeout=15) as response:
        return response.read()


def replicas(uri, expected):
    key = location(uri).removeprefix("s3://")
    metadata = json.loads(
        get("http://filer1:8888/buckets/" + quote(key, safe="/") + "?metadata=true")
    )
    chunks = sorted(metadata.get("chunks") or [], key=lambda c: int(c.get("offset", 0)))
    if not chunks:
        raise ValueError("Data Parquet requires observable direct chunks")
    hashes = [hashlib.sha256() for _ in range(3)]
    offset = 0
    for chunk in chunks:
        if (
            chunk.get("is_chunk_manifest")
            or chunk.get("cipher_key")
            or int(chunk.get("offset", 0)) != offset
        ):
            raise ValueError("Unsupported/overlapping native chunk layout")
        fid = chunk["file_id"]
        owners = json.loads(
            get("http://master1:9333/dir/lookup?volumeId=" + fid.split(",")[0])
        )["locations"]
        if {r["url"] for r in owners} != {f"volume{i}:8080" for i in range(1, 4)}:
            raise ValueError("Not three native volume owners")
        for i in range(3):
            data = get(f"http://volume{i + 1}:8080/{quote(fid, safe=',')}?proxied=true")
            if len(data) != int(chunk["size"]):
                raise ValueError("Native replica length mismatch")
            hashes[i].update(data)
        offset += int(chunk["size"])
    digest = hashlib.sha256(expected).hexdigest()
    if offset != len(expected) or any(h.hexdigest() != digest for h in hashes):
        raise ValueError("Native replica body mismatch")
    return len(chunks) * 3, offset * 3


def verify(args):
    table = load(args.metadata)
    snap = (
        table.snapshot_by_id(args.snapshot)
        if args.snapshot
        else table.current_snapshot()
    )
    if snap is None:
        raise ValueError("No committed snapshot")
    total = int(os.environ["ORDER_COUNT"])
    customers = int(os.environ["CUSTOMER_COUNT"])
    journals = {phase: witness(phase, total) for phase in args.phases.split(",")}
    counts = dict(
        rows=0,
        files=0,
        file_bytes=0,
        column_uncompressed_bytes=0,
        column_compressed_bytes=0,
        native_replica_reads=0,
        native_replica_bytes=0,
    )
    for task in table.scan(snapshot_id=snap.snapshot_id).plan_files():
        if task.delete_files:
            raise ValueError(
                "Delete-file semantics outside this append/rewrite contract"
            )
        raw = body(table, task.file.file_path)
        if len(raw) != task.file.file_size_in_bytes:
            raise ValueError("Iceberg/Parquet file length mismatch")
        parquet = pq.ParquetFile(io.BytesIO(raw))
        if set(parquet.schema_arrow.names) != set(FIELDS):
            raise ValueError("Unexpected output schema")
        rows = 0
        for batch in parquet.iter_batches(batch_size=32768, columns=list(FIELDS)):
            for row in zip(*(batch.column(i).to_pylist() for i in range(len(FIELDS)))):
                check_row(row, task.file.partition, journals, customers)
                rows += 1
        if rows != task.file.record_count:
            raise ValueError("Manifest record_count mismatch")
        for group in range(parquet.metadata.num_row_groups):
            rg = parquet.metadata.row_group(group)
            for column in range(rg.num_columns):
                c = rg.column(column)
                if c.compression != "ZSTD":
                    raise ValueError("Expected native ZSTD codec")
                counts["column_uncompressed_bytes"] += c.total_uncompressed_size
                counts["column_compressed_bytes"] += c.total_compressed_size
        if args.replicas:
            reads, size = replicas(task.file.file_path, raw)
            counts["native_replica_reads"] += reads
            counts["native_replica_bytes"] += size
        counts["rows"] += rows
        counts["files"] += 1
        counts["file_bytes"] += len(raw)
    for phase, (_, _, seen) in journals.items():
        if sum(seen) != total:
            raise ValueError(
                f"Missing acknowledged rows in {phase}: {sum(seen)}/{total}"
            )
    counts["compression_ratio"] = (
        counts["column_uncompressed_bytes"] / counts["column_compressed_bytes"]
    )
    print(
        json.dumps(
            dict(
                passed=True,
                metadata=args.metadata,
                snapshot=snap.snapshot_id,
                parent=snap.parent_snapshot_id,
                phases=list(journals),
                **counts,
            )
        ),
        flush=True,
    )


def summary(args):
    table = load(args.metadata)
    snap = table.current_snapshot()
    print(
        json.dumps(
            dict(
                metadata=args.metadata,
                snapshot=snap.snapshot_id if snap else None,
                summary=snap.summary.model_dump(mode="json") if snap else {},
                table_uuid=str(table.metadata.table_uuid),
                snapshots=[
                    dict(
                        id=s.snapshot_id,
                        parent=s.parent_snapshot_id,
                        timestamp_ms=s.timestamp_ms,
                        summary=s.summary.model_dump(mode="json"),
                    )
                    for s in table.metadata.snapshots
                ],
            )
        ),
        flush=True,
    )


def watch(args):
    table = load(args.metadata)
    tasks = list(table.scan().plan_files())
    if not tasks:
        raise ValueError("Live reader requires a committed baseline")
    selected = tasks[0].file.file_path
    expected = hashlib.sha256(body(table, selected)).digest()
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    deadline = time.monotonic() + 600
    while not stopped and time.monotonic() < deadline:
        start = time.monotonic()
        try:
            raw = body(table, selected)
            if hashlib.sha256(raw).digest() != expected:
                raise ValueError("Pinned baseline object changed")
            print(
                json.dumps(
                    dict(
                        read="ok",
                        bytes=len(raw),
                        wall_ms=(time.monotonic() - start) * 1000,
                        at_ns=time.time_ns(),
                        metadata=args.metadata,
                    )
                ),
                flush=True,
            )
        except Exception as error:
            print(
                json.dumps(dict(read="fail", error=str(error), at_ns=time.time_ns())),
                flush=True,
            )
            raise
        for _ in range(20):
            if stopped:
                break
            time.sleep(0.1)


def retention(args):
    """Conservative candidates only. Never expire files during a concurrent writer."""
    table = load(args.metadata)
    listed = json.loads(Path(args.inventory).read_text())
    manifests, referenced = set(), set()
    for snapshot in table.metadata.snapshots:
        for manifest in snapshot.manifests(table.io):
            if manifest.manifest_path in manifests:
                continue
            manifests.add(manifest.manifest_path)
            # Include deleted entries conservatively; an old snapshot may still need them.
            for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=False):
                referenced.add(location(entry.data_file.file_path))
    data = {
        "s3://warehouse/" + row["key"]: row["bytes"]
        for row in listed["objects"]
        if row["key"].endswith(".parquet")
    }
    missing = referenced - data.keys()
    if missing:
        raise ValueError(
            "Retained snapshots reference missing data files: " + str(missing)
        )
    candidates = {key: size for key, size in data.items() if key not in referenced}
    print(
        json.dumps(
            dict(
                retained_snapshots=len(table.metadata.snapshots),
                unique_manifests=len(manifests),
                retained_data_files=len(referenced),
                listed_parquet_files=len(data),
                candidate_unreferenced_data_files=candidates,
                candidate_bytes=sum(candidates.values()),
                logical_object_bytes=listed["logical_object_bytes"],
                deleted_files=0,
                passed=True,
                limitation="Conservative retained-manifest union; candidates are not proven safe to delete",
            )
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("verify", "summary", "watch", "retention"))
    parser.add_argument("metadata")
    parser.add_argument("--phases", default="baseline")
    parser.add_argument("--snapshot", type=int)
    parser.add_argument("--replicas", action="store_true")
    parser.add_argument("--inventory")
    args = parser.parse_args()
    start = time.monotonic()
    try:
        globals()[args.mode](args)
    finally:
        counters = {
            n: (Path("/sys/fs/cgroup") / n).read_text()
            for n in (
                "cpu.stat",
                "memory.current",
                "memory.peak",
                "memory.events",
                "io.stat",
            )
        }
        print(
            json.dumps(
                dict(actor_counters=counters, oracle_wall_s=time.monotonic() - start)
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
