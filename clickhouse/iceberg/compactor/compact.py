"""One-shot Iceberg maintenance owner. Never expires snapshots or deletes orphans."""

import json
import os
from pathlib import Path
import re
import time
import uuid

from resources import Sampler


def save_json(path, value, *, replace=False):
    """Unique fsynced reports; retain the established successful-result filename."""
    temporary = (
        path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp") if replace else path
    )
    # Incomplete unique reports remain visible for diagnosis.
    with temporary.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_spark():
    from pyspark.sql import SparkSession

    return (
        SparkSession.builder.appName("iceberg-compaction")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.lake.type", "rest")
        .config(
            "spark.sql.catalog.lake.uri",
            os.environ.get("CATALOG_URL", "http://storage:8181"),
        )
        .config("spark.sql.catalog.lake.warehouse", "warehouse")
        .config("spark.sql.catalog.lake.credential", "demo:demo")
        .config("spark.sql.catalog.lake.rest.auth.type", "oauth2")
        .config(
            "spark.sql.catalog.lake.oauth2-server-uri",
            "http://storage:8181/v1/oauth/tokens",
        )
        .config("spark.sql.catalog.lake.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config("spark.sql.catalog.lake.s3.endpoint", "http://storage:8333")
        .config("spark.sql.catalog.lake.s3.path-style-access", "true")
        .config("spark.sql.catalog.lake.s3.access-key-id", "demo")
        .config("spark.sql.catalog.lake.s3.secret-access-key", "demo")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )


def metrics(spark, name):
    files = (
        spark.sql(
            f"SELECT count(*) files, coalesce(sum(file_size_in_bytes),0) bytes, "
            f"coalesce(min(file_size_in_bytes),0) min_bytes, coalesce(max(file_size_in_bytes),0) max_bytes "
            f"FROM lake.{name}.files"
        )
        .first()
        .asDict()
    )
    files["snapshot"] = spark.sql(
        f"SELECT snapshot_id FROM lake.{name}.refs WHERE name='main'"
    ).first()[0]
    files["rows"] = spark.sql(f"SELECT count(*) FROM lake.{name}").first()[0]
    return files


def compact(spark, name, target, result):
    result.update(
        table=name,
        target_file_bytes=target,
        spark_version=spark.version,
        iceberg_version="1.10.1",
        before=metrics(spark, name),
    )
    start = time.monotonic()
    result["rewrite"] = [
        r.asDict()
        for r in spark.sql(
            f"CALL lake.system.rewrite_data_files(table => '{name}', strategy => 'binpack', "
            f"options => map('target-file-size-bytes','{target}','min-input-files','2'))"
        ).collect()
    ]
    result["manifests"] = [
        r.asDict()
        for r in spark.sql(f"CALL lake.system.rewrite_manifests('{name}')").collect()
    ]
    result["seconds"] = time.monotonic() - start
    result["after"] = metrics(spark, name)
    if result["before"]["rows"] != result["after"]["rows"]:
        raise RuntimeError("compaction changed the row count")
    old = result["before"]["snapshot"]
    result["retained_snapshot_rows"] = spark.sql(
        f"SELECT count(*) FROM lake.{name} VERSION AS OF {old}"
    ).first()[0]
    if result["retained_snapshot_rows"] != result["before"]["rows"]:
        raise RuntimeError("retained snapshot changed")


def run(output=Path("/results")):
    identifier = str(uuid.uuid4())
    report_path = output / ("compaction-" + identifier + "-resources.json")
    samples_path = output / ("compaction-" + identifier + "-resources.jsonl")
    report = {
        "run_id": identifier,
        "started_ns": time.time_ns(),
        "passed": False,
        "samples_file": samples_path.name,
        "phases": {},
        "errors": [],
        "compression": {
            "available": False,
            "reason": "Iceberg data-file sizes before/after rewriting are object bytes, not encoded Parquet compression. This job does not sum Parquet compressed/uncompressed column chunks.",
        },
        "disk_allocation": {
            "available": False,
            "reason": "S3 data lives in the storage service. Container cgroup block bytes/IOPS and filesystem free space are measured; per-job allocated scratch and remote physical storage are not measured.",
        },
    }
    sampler, spark, primary, result = None, None, None, {}

    def failure(stage, error):
        nonlocal primary
        report["errors"].append({"stage": stage, "error": repr(error)})
        if primary is None:
            primary = error
        elif hasattr(primary, "add_note"):
            primary.add_note(stage + ": " + repr(error))

    try:
        sampler = Sampler(samples_path)
        sampler.start()
        sampler.attach_parent_jvm()
        name = os.environ.get("TABLE", "lab.probe")
        target = int(os.environ.get("TARGET_FILE_BYTES", "134217728"))
        if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name):
            raise ValueError("TABLE must be a simple namespace.table")
        if target < 1048576:
            raise ValueError("TARGET_FILE_BYTES must be at least 1 MiB")
        report.update(table=name, target_file_bytes=target)
        report["phases"]["spark_startup_started_ns"] = time.time_ns()
        spark = build_spark()
        report["phases"]["spark_startup_completed_ns"] = time.time_ns()
        # RuntimeMXBean.getPid is the actual JVM PID (JDK10+), not Python's PID.
        pid = int(
            spark.sparkContext._jvm.java.lang.management.ManagementFactory.getRuntimeMXBean().getPid()
        )
        sampler.attach_jvm(pid)
        spark.sparkContext.setLogLevel("WARN")
        report["phases"]["compaction_started_ns"] = time.time_ns()
        compact(spark, name, target, result)
        report["phases"]["compaction_completed_ns"] = time.time_ns()
        save_json(
            output / (name.replace(".", "-") + "-compaction.json"), result, replace=True
        )
    except BaseException as error:
        failure("startup-or-compaction", error)
    finally:
        report["compaction_result"] = result
        report["stop"] = {"attempted": spark is not None, "started_ns": time.time_ns()}
        if spark is not None:
            try:
                if sampler is not None:
                    sampler.capture("before-spark-stop")
            except BaseException as error:
                failure("before-stop-resource-sample", error)
            try:
                spark.stop()
                report["stop"]["passed"] = True
            except BaseException as error:
                report["stop"].update(passed=False, error=repr(error))
                failure("spark-stop", error)
        else:
            report["stop"].update(
                passed=False,
                reason="SparkSession was not returned; no session handle to stop",
            )
        report["stop"]["completed_ns"] = time.time_ns()
        if sampler is not None:
            try:
                report["resources"] = sampler.close()
            except BaseException as error:
                failure("resource-finalization", error)
        report.update(passed=primary is None, completed_ns=time.time_ns())
        try:
            save_json(report_path, report)
        except BaseException as error:
            failure("resource-report-write", error)
    if primary is not None:
        raise primary
    print(json.dumps(result, indent=2))
    return report_path


if __name__ == "__main__":
    run()
