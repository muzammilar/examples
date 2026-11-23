"""Read-only native Spark compatibility gate before changing a ClickHouse-written table."""

import json
import hashlib
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, "/opt/lab")
import compact
from resources import Sampler


def main():
    root = Path("/results")
    config = json.loads((root / "input.json").read_text())
    report = {
        "passed": False,
        "started_ns": time.time_ns(),
        "scope": "Read-only Spark/Iceberg body and pinned-snapshot compatibility, not compaction or concurrency qualification.",
    }
    spark, failure = None, None
    sampler = Sampler(root / "spark-resources.jsonl")
    sampler.start()
    sampler.attach_parent_jvm()
    try:
        spark = compact.build_spark()
        spark.sparkContext.setLogLevel("WARN")
        sampler.attach_jvm(
            int(
                spark.sparkContext._jvm.java.lang.management.ManagementFactory.getRuntimeMXBean().getPid()
            )
        )
        report["versions"] = compact.native_versions(spark)
        if (
            report["versions"]["spark_version"] != "4.1.4"
            or report["versions"]["iceberg_version"] != "1.12.0"
        ):
            raise RuntimeError("unexpected native maintenance versions")
        table = config["table"]
        first, rows, snapshot = (
            config["first_id"],
            config["rows"],
            config["snapshot_id"],
        )
        sql = f"SELECT count(*) AS rows,count(DISTINCT event_id) AS unique_ids,min(event_id) AS first_id,max(event_id) AS last_id,sum(CASE WHEN event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR event_id<{first} OR event_id>={first + rows} OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=sha2(CAST(event_id AS STRING),256) THEN 1 ELSE 0 END) AS invalid_bodies FROM lake.lab.{table} VERSION AS OF {snapshot}"
        compact.save_json(root / "native-query.json", {"sql": sql})
        started = time.monotonic_ns()
        report["body"] = spark.sql(sql).first().asDict()
        report["body_query_ns"] = time.monotonic_ns() - started
        if report["body"] != {
            "rows": rows,
            "unique_ids": rows,
            "first_id": first,
            "last_id": first + rows - 1,
            "invalid_bodies": 0,
        }:
            raise RuntimeError("complete native Spark body oracle failed")
        native = spark.sparkContext._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(
            spark._jsparkSession, "lake.lab." + table
        )
        native.refresh()
        planned = native.newScan().useSnapshot(snapshot).planFiles()
        iterator = planned.iterator()
        compression = {
            "files": 0,
            "file_bytes": 0,
            "physical_rows": 0,
            "column_chunks": 0,
            "compressed_bytes": 0,
            "uncompressed_bytes": 0,
            "local_copies": [],
        }
        try:
            while iterator.hasNext():
                data = iterator.next().file()
                size, location = int(data.fileSizeInBytes()), str(data.location())
                if (
                    size > 2097152
                    or compression["files"] >= 16
                    or not location.startswith("s3://warehouse/lab/" + table + "-")
                ):
                    raise RuntimeError(
                        "small compatibility footer audit exceeded its scoped file budget"
                    )
                stream = native.io().newInputFile(location).newStream()
                try:
                    contents = bytes(stream.readAllBytes())
                finally:
                    stream.close()
                if len(contents) != size:
                    raise RuntimeError(
                        "native S3 file length differs from the pinned manifest"
                    )
                local = root / (
                    "footer-input-" + str(compression["files"]) + ".parquet"
                )
                with local.open("xb") as output:
                    output.write(contents)
                footer = spark.sparkContext._jvm.org.apache.parquet.hadoop.ParquetFileReader.readFooter(
                    spark.sparkContext._jsc.hadoopConfiguration(),
                    spark.sparkContext._jvm.org.apache.hadoop.fs.Path(local.as_uri()),
                )
                for block in footer.getBlocks():
                    compression["physical_rows"] += int(block.getRowCount())
                    for column in block.getColumns():
                        compression["column_chunks"] += 1
                        compression["compressed_bytes"] += int(column.getTotalSize())
                        compression["uncompressed_bytes"] += int(
                            column.getTotalUncompressedSize()
                        )
                compression["files"] += 1
                compression["file_bytes"] += size
                compression["local_copies"].append(
                    {"file": local.name, "sha256": hashlib.sha256(contents).hexdigest()}
                )
        finally:
            iterator.close()
            planned.close()
        if compression["physical_rows"] != rows or compression["compressed_bytes"] <= 0:
            raise RuntimeError("native pinned-file footer rows/compression differ")
        compression["encoded_chunk_ratio"] = (
            compression["uncompressed_bytes"] / compression["compressed_bytes"]
        )
        compression["scope"] = (
            "Pinned Iceberg scan membership; native S3FileIO downloads <=2 MiB per file, then the existing Parquet reader inspects local footers. Includes copy overhead; not an application or capacity benchmark."
        )
        report["compression"] = compression
        manifests = native.snapshot(snapshot).allManifests(native.io())
        if len(manifests) > 16:
            raise RuntimeError("manifest header audit exceeded the small-probe bound")
        headers = []
        for manifest in manifests:
            location = str(manifest.path())
            if (
                not location.startswith("s3://warehouse/lab/" + table + "-")
                or int(manifest.length()) > 2097152
            ):
                raise RuntimeError(
                    "manifest header audit escaped the pinned small fixture"
                )
            stream = native.io().newInputFile(location).newStream()
            reader = None
            try:
                reader = spark.sparkContext._jvm.org.apache.avro.file.DataFileStream(
                    stream,
                    spark.sparkContext._jvm.org.apache.avro.generic.GenericDatumReader(),
                )
                keys = [str(key) for key in reader.getMetaKeys()]
                headers.append(
                    {
                        "location": location,
                        "header_keys": keys,
                        "format_version": str(reader.getMetaString("format-version"))
                        if "format-version" in keys
                        else None,
                        "content": str(reader.getMetaString("content"))
                        if "content" in keys
                        else None,
                    }
                )
            finally:
                if reader is not None:
                    reader.close()
                else:
                    stream.close()
        report["manifest_metadata"] = headers
        report["v2_content_header_missing"] = any(
            item["format_version"] == "2" and item["content"] is None
            for item in headers
        )
    except BaseException as error:
        failure = error
        report["error"] = repr(error)
        report["traceback"] = traceback.format_exc()
    finally:
        if spark is not None:
            try:
                spark.stop()
            except BaseException as error:
                report["spark_stop_error"] = repr(error)
                failure = failure or error
        try:
            report["resources"] = sampler.close()
        except BaseException as error:
            report["resource_error"] = repr(error)
            failure = failure or error
        report["passed"] = failure is None
        report["completed_ns"] = time.time_ns()
        compact.save_json(root / "job-result.json", report)
    if failure:
        raise failure


if __name__ == "__main__":
    main()
