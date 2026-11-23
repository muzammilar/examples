"""Offline engine compatibility control; not the S3/live-chaos qualification."""

import hashlib
import struct
import sys
import time
from pathlib import Path


def finish(report, spark, sampler, save, started, primary):
    def failure(stage, error):
        nonlocal primary
        report.setdefault("errors", []).append({"stage": stage, "error": repr(error)})
        if primary is None:
            primary = error
        else:
            primary.add_note(stage + ": " + repr(error))
        report["passed"] = False
        report["error"] = repr(primary)

    try:
        if spark is not None:
            spark.stop()
    except BaseException as error:
        failure("spark-stop", error)
    try:
        report["resources"] = sampler.close()
    except BaseException as error:
        failure("sampler-close", error)
    report["wall_seconds"] = time.monotonic() - started
    report["passed"] = primary is None
    if primary is not None:
        report["error"] = repr(primary)
    try:
        save()
    except BaseException as error:
        failure("save-final-report", error)
    return primary


def run():
    sys.path.insert(0, "/opt/lab")
    import compact
    from resources import Sampler
    from pyspark.sql import SparkSession

    out = Path("/results")
    report = {
        "passed": False,
        "seed_rows": 2000000,
        "append_rows": 100000,
        "customers": 10000,
        "scope": "One offline local Hadoop catalog, no S3/catalog server, faults or concurrent writers",
        "phases": {},
    }
    sampler = Sampler(out / "resources.jsonl")
    sampler.start()
    sampler.attach_parent_jvm()
    spark = None
    started = time.monotonic()
    primary = None

    def save():
        compact.save_json(out / "result.json", report, replace=True)

    def timed(name, fn):
        start = time.monotonic()
        value = fn()
        report["phases"][name] = {"seconds": time.monotonic() - start}
        save()
        return value

    def expected(n):
        sha = hashlib.sha256()
        for i in range(n):
            sha.update(struct.pack("<qqq", i, i % 10000, i * 7 + 3))
        return sha.hexdigest()

    def verify(n, snapshot=None):
        table = "lake.lab.probe" + (
            " VERSION AS OF " + str(snapshot) if snapshot else ""
        )
        digest = hashlib.sha256()
        count = 0
        for row in spark.sql(
            "SELECT id,customer,value FROM " + table + " ORDER BY id"
        ).toLocalIterator():
            assert tuple(row) == (count, count % 10000, count * 7 + 3), (
                "Wrong body",
                count,
                tuple(row),
            )
            digest.update(struct.pack("<qqq", *row))
            count += 1
        assert count == n and digest.hexdigest() == expected(n)
        return {
            "rows": count,
            "canonical_sha256": digest.hexdigest(),
            "encoding": "little-endian signed int64 id/customer/value, ordered by id",
        }

    def files():
        rows = spark.sql(
            "SELECT file_path,file_size_in_bytes FROM lake.lab.probe.files"
        ).collect()
        compressed = uncompressed = 0
        j = spark.sparkContext._jvm
        for row in rows:
            footer = j.org.apache.parquet.hadoop.ParquetFileReader.readFooter(
                spark.sparkContext._jsc.hadoopConfiguration(),
                j.org.apache.hadoop.fs.Path(row.file_path),
            )
            for block in footer.getBlocks():
                for column in block.getColumns():
                    compressed += int(column.getTotalSize())
                    uncompressed += int(column.getTotalUncompressedSize())
        return {
            "count": len(rows),
            "file_bytes": sum(r.file_size_in_bytes for r in rows),
            "compressed_chunk_bytes": compressed,
            "uncompressed_chunk_bytes": uncompressed,
            "chunk_compression_ratio": uncompressed / compressed
            if compressed
            else None,
        }

    try:
        spark = (
            SparkSession.builder.appName("iceberg-current-offline-smoke")
            .config("spark.driver.host", "127.0.0.1")
            .config("spark.driver.bindAddress", "127.0.0.1")
            .config(
                "spark.sql.extensions",
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
            )
            .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
            .config("spark.sql.catalog.lake.type", "hadoop")
            .config("spark.sql.catalog.lake.warehouse", "file:///data/warehouse")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.adaptive.enabled", "false")
            .getOrCreate()
        )
        spark.sparkContext.setLogLevel("WARN")
        sampler.attach_jvm(
            int(
                spark.sparkContext._jvm.java.lang.management.ManagementFactory.getRuntimeMXBean().getPid()
            )
        )
        report["versions"] = compact.native_versions(spark)
        assert (
            report["versions"]["spark_version"] == "4.1.4"
            and report["versions"]["iceberg_version"] == "1.12.0"
        )
        spark.sql("CREATE NAMESPACE lake.lab")
        spark.sql(
            "CREATE TABLE lake.lab.probe (id BIGINT, customer BIGINT, value BIGINT) USING iceberg TBLPROPERTIES ('format-version'='2')"
        )

        def seed():
            for start in range(0, 2000000, 100000):
                spark.sql(
                    f"INSERT INTO lake.lab.probe SELECT id, id % 10000, id * 7 + 3 FROM range({start},{start + 100000},1,2)"
                )

        timed("seed", seed)
        report["before"] = files()
        report["before_body"] = timed("verify_before", lambda: verify(2000000))
        pin = spark.sql(
            "SELECT snapshot_id FROM lake.lab.probe.snapshots ORDER BY committed_at DESC LIMIT 1"
        ).first()[0]
        report["pin"] = pin

        def rewrite():
            return [
                r.asDict()
                for r in spark.sql(
                    "CALL lake.system.rewrite_data_files(table => 'lab.probe', strategy => 'binpack', options => map('target-file-size-bytes','134217728','min-input-files','2','partial-progress.enabled','false','max-concurrent-file-group-rewrites','1'))"
                ).collect()
            ]

        report["rewrite"] = timed("rewrite", rewrite)
        report["after"] = files()
        assert report["after"]["count"] < report["before"]["count"]
        report["after_body"] = timed("verify_after", lambda: verify(2000000))
        spark.sql(
            "INSERT INTO lake.lab.probe SELECT id, id % 10000, id * 7 + 3 FROM range(2000000,2100000,1,2)"
        )
        report["current_body"] = timed("verify_append", lambda: verify(2100000))
        report["retained_body"] = timed("verify_retained", lambda: verify(2000000, pin))
        report["allocated_data_bytes"] = sum(
            p.stat().st_blocks * 512 for p in Path("/data").rglob("*")
        )
    except BaseException as error:
        primary = error
    finally:
        primary = finish(report, spark, sampler, save, started, primary)
    if primary is not None:
        raise primary


if __name__ == "__main__":
    run()
