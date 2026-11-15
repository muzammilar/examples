"""One-shot Iceberg maintenance owner. Never expires snapshots or deletes orphans."""
import json
import os
import re
import time
from pathlib import Path
from pyspark.sql import SparkSession

name = os.environ.get("TABLE", "lab.probe")
if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name):
    raise ValueError("TABLE must be a simple namespace.table")
target = int(os.environ.get("TARGET_FILE_BYTES", "134217728"))
if target < 1048576:
    raise ValueError("TARGET_FILE_BYTES must be at least 1 MiB")
spark = (SparkSession.builder.appName("iceberg-compaction")
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.lake.type", "rest")
    .config("spark.sql.catalog.lake.uri", os.environ.get("CATALOG_URL", "http://storage:8181"))
    .config("spark.sql.catalog.lake.warehouse", "warehouse")
    .config("spark.sql.catalog.lake.credential", "demo:demo")
    .config("spark.sql.catalog.lake.rest.auth.type", "oauth2")
    .config("spark.sql.catalog.lake.oauth2-server-uri", "http://storage:8181/v1/oauth/tokens")
    .config("spark.sql.catalog.lake.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    .config("spark.sql.catalog.lake.s3.endpoint", "http://storage:8333")
    .config("spark.sql.catalog.lake.s3.path-style-access", "true")
    .config("spark.sql.catalog.lake.s3.access-key-id", "demo")
    .config("spark.sql.catalog.lake.s3.secret-access-key", "demo")
    .config("spark.sql.shuffle.partitions", "2")
    .getOrCreate())
spark.sparkContext.setLogLevel("WARN")

def metrics():
    files = spark.sql(f"SELECT count(*) files, coalesce(sum(file_size_in_bytes),0) bytes, "
                      f"coalesce(min(file_size_in_bytes),0) min_bytes, coalesce(max(file_size_in_bytes),0) max_bytes "
                      f"FROM lake.{name}.files").first().asDict()
    files["snapshot"] = spark.sql(f"SELECT snapshot_id FROM lake.{name}.refs WHERE name='main'").first()[0]
    files["rows"] = spark.sql(f"SELECT count(*) FROM lake.{name}").first()[0]
    return files

try:
    result = {"table": name, "target_file_bytes": target, "spark_version": spark.version, "iceberg_version": "1.10.1", "before": metrics()}
    start = time.monotonic()
    result["rewrite"] = [r.asDict() for r in spark.sql(
        f"CALL lake.system.rewrite_data_files(table => '{name}', strategy => 'binpack', "
        f"options => map('target-file-size-bytes','{target}','min-input-files','2'))").collect()]
    result["manifests"] = [r.asDict() for r in spark.sql(f"CALL lake.system.rewrite_manifests('{name}')").collect()]
    result["seconds"] = time.monotonic() - start
    result["after"] = metrics()
    if result["before"]["rows"] != result["after"]["rows"]:
        raise RuntimeError("compaction changed the row count")
    old = result["before"]["snapshot"]
    result["retained_snapshot_rows"] = spark.sql(f"SELECT count(*) FROM lake.{name} VERSION AS OF {old}").first()[0]
    if result["retained_snapshot_rows"] != result["before"]["rows"]:
        raise RuntimeError("retained snapshot changed")
    output = Path("/results") / (name.replace(".", "-") + "-compaction.json")
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
finally:
    spark.stop()
