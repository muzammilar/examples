"""One-shot Iceberg maintenance owner. Never expires snapshots or deletes orphans."""

import json
import os
from pathlib import Path
import re
import time
import uuid

from resources import Sampler


RESULTS_ROOT = Path("/results")


SOURCES = {
    "table_refresh": "https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/spark/v4.0/spark/src/main/java/org/apache/iceberg/spark/Spark3Util.java#L665-L679",
    "snapshot_scan": "https://iceberg.apache.org/javadoc/1.10.1/org/apache/iceberg/TableScan.html#useSnapshot(long)",
    "time_travel": "https://iceberg.apache.org/docs/1.10.1/spark-queries/#time-travel-queries-with-sql",
    "rewrite_options": "https://iceberg.apache.org/docs/1.10.1/spark-procedures/#general-options",
    "s3_http_properties": "https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/aws/src/main/java/org/apache/iceberg/aws/HttpClientProperties.java#L85-L95",
    "s3_http_timeout": "https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/aws/src/main/java/org/apache/iceberg/aws/ApacheHttpClientConfigurations.java#L49-L87",
    "table_io_overrides": "https://github.com/apache/iceberg/blob/apache-iceberg-1.10.1/core/src/main/java/org/apache/iceberg/rest/RESTSessionCatalog.java#L914-L922",
}


def results_directory():
    value = os.environ.get("COMPACTION_RESULTS_DIR", str(RESULTS_ROOT))
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(value) != value:
        raise ValueError(
            "COMPACTION_RESULTS_DIR must be an absolute normalized path inside /results"
        )
    try:
        path.relative_to(RESULTS_ROOT)
        resolved = path.resolve(strict=True)
        resolved.relative_to(RESULTS_ROOT.resolve(strict=True))
    except (ValueError, OSError) as error:
        raise ValueError(
            "COMPACTION_RESULTS_DIR must be an existing directory inside /results"
        ) from error
    if not resolved.is_dir():
        raise ValueError(
            "COMPACTION_RESULTS_DIR must be an existing directory inside /results"
        )
    return resolved


def boolean(name, default):
    value = os.environ.get(name, "true" if default else "false")
    if value not in ("true", "false"):
        raise ValueError(name + " must be true or false")
    return value == "true"


def configuration():
    endpoint = os.environ.get("COMPACTOR_S3_ENDPOINT", "http://storage:8333")
    if endpoint not in ("http://storage:8333", "http://compaction-upload-gate:8333"):
        raise ValueError("COMPACTOR_S3_ENDPOINT must be an allowed local lab service")
    catalog = os.environ.get("CATALOG_URL", "http://storage:8181")
    if catalog != "http://storage:8181":
        raise ValueError("CATALOG_URL must be http://storage:8181")
    return {
        "live_writers": boolean("COMPACTION_LIVE_WRITERS", False),
        "rewrite_manifests": boolean("REWRITE_MANIFESTS", True),
        "s3_endpoint": endpoint,
        "catalog_url": catalog,
        "http_client_properties": {
            "http-client.type": "apache",
            "http-client.apache.socket-timeout-ms": "180000",
        }
        if endpoint == "http://compaction-upload-gate:8333"
        else {},
        "http_timeout_scope": "socket inactivity only, not a total operation/job deadline; unconfigured properties retain SDK defaults",
        "rewrite_options": {
            "partial-progress.enabled": "false",
            "max-concurrent-file-group-rewrites": "1",
        },
    }


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
    config = configuration()
    from pyspark.sql import SparkSession

    builder = (
        SparkSession.builder.appName("iceberg-compaction")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.lake.type", "rest")
        .config(
            "spark.sql.catalog.lake.uri",
            config["catalog_url"],
        )
        .config("spark.sql.catalog.lake.warehouse", "warehouse")
        .config("spark.sql.catalog.lake.credential", "demo:demo")
        .config("spark.sql.catalog.lake.rest.auth.type", "oauth2")
        .config(
            "spark.sql.catalog.lake.oauth2-server-uri",
            "http://storage:8181/v1/oauth/tokens",
        )
        .config("spark.sql.catalog.lake.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config("spark.sql.catalog.lake.s3.endpoint", config["s3_endpoint"])
        .config("spark.sql.catalog.lake.s3.path-style-access", "true")
        .config("spark.sql.catalog.lake.s3.access-key-id", "demo")
        .config("spark.sql.catalog.lake.s3.secret-access-key", "demo")
        .config("spark.sql.shuffle.partitions", "2")
    )
    for name, value in config["http_client_properties"].items():
        builder = builder.config("spark.sql.catalog.lake." + name, value)
    return builder.getOrCreate()


def metrics(spark, name, *, effective_http=None):
    # The catalog can cache TableOperations. Refresh once, then pin *both* scans;
    # separate current refs/files/count queries can observe three different heads.
    table = (
        spark.sparkContext._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(
            spark._jsparkSession, "lake." + name
        )
    )
    configured = configuration()["http_client_properties"]
    observed = None
    if configured:
        # REST table config can override catalog properties. Inspect only these
        # two nonsecret settings; never dump FileIO properties/credentials.
        properties = table.io().properties()
        observed = {key: properties.get(key) for key in configured}
        if effective_http is not None:
            effective_http.update(observed)
        if observed != configured:
            raise RuntimeError(
                "effective gated S3 HTTP settings differ: " + repr(observed)
            )
    table.refresh()
    snapshot = table.currentSnapshot()
    if snapshot is None:
        raise RuntimeError("compaction requires a table with a committed snapshot")
    identifier = int(snapshot.snapshotId())
    planned = table.newScan().useSnapshot(identifier).planFiles()
    iterator, primary, membership = None, None, []
    try:
        iterator = planned.iterator()
        while iterator.hasNext():
            data = iterator.next().file()
            location = str(data.location())
            if not location.startswith("s3://warehouse/"):
                raise ValueError("data file is outside the lab warehouse: " + location)
            membership.append(
                {
                    "location": location,
                    "bytes": int(data.fileSizeInBytes()),
                    "records": int(data.recordCount()),
                }
            )
    except BaseException as error:
        primary = error
    finally:
        for resource in (iterator, planned):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    if primary is None:
                        primary = error
                    elif hasattr(primary, "add_note"):
                        primary.add_note("closing snapshot file scan: " + repr(error))
    if primary is not None:
        raise primary
    sizes = [item["bytes"] for item in membership]
    return {
        "snapshot": identifier,
        "snapshot_manifest_list": str(snapshot.manifestListLocation()),
        "files": len(membership),
        "bytes": sum(sizes),
        "min_bytes": min(sizes, default=0),
        "max_bytes": max(sizes, default=0),
        "file_membership": sorted(membership, key=lambda item: item["location"]),
        "file_scope": "data files in TableScan.useSnapshot(snapshot); excludes delete files",
        "http_client_properties": observed,
        "rows": snapshot_rows(spark, name, identifier),
    }


def snapshot_rows(spark, name, identifier):
    return int(
        spark.sql(
            f"SELECT count(*) FROM lake.{name} VERSION AS OF {identifier}"
        ).first()[0]
    )


def native_versions(spark):
    build = spark.sparkContext._jvm.org.apache.iceberg.IcebergBuild
    version, commit = build.version(), build.gitCommitId()
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", version
    ):
        raise RuntimeError("native Iceberg release version is unavailable or unstable")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("native Iceberg commit is unavailable")
    return {
        "spark_version": spark.version,
        "iceberg_version": version,
        "iceberg_commit": commit,
    }


def source_references(versions):
    # References follow the loaded release; baseline line anchors do not transfer.
    spark_line = ".".join(versions["spark_version"].split(".")[:2])
    return {
        key: url.split("#")[0]
        .replace("1.10.1", versions["iceberg_version"])
        .replace("/spark/v4.0/", "/spark/v" + spark_line + "/")
        for key, url in SOURCES.items()
    }


def compact(spark, name, target, result):
    config = configuration()
    versions = native_versions(spark)
    result.update(
        **versions,
        table=name,
        target_file_bytes=target,
        configuration=config,
        sources=source_references(versions),
        effective_http_client_properties={},
    )
    result["before"] = metrics(
        spark, name, effective_http=result["effective_http_client_properties"]
    )
    start = time.monotonic()
    result["rewrite"] = [
        r.asDict()
        for r in spark.sql(
            f"CALL lake.system.rewrite_data_files(table => '{name}', strategy => 'binpack', "
            f"options => map('target-file-size-bytes','{target}','min-input-files','2',"
            "'partial-progress.enabled','false','max-concurrent-file-group-rewrites','1'))"
        ).collect()
    ]
    result["data_rewrite_seconds"] = time.monotonic() - start
    result["manifests"] = []
    result["manifest_rewrite_seconds"] = None
    if config["rewrite_manifests"]:
        manifests_started = time.monotonic()
        result["manifests"] = [
            r.asDict()
            for r in spark.sql(
                f"CALL lake.system.rewrite_manifests('{name}')"
            ).collect()
        ]
        result["manifest_rewrite_seconds"] = time.monotonic() - manifests_started
    result["seconds"] = time.monotonic() - start
    result["after"] = metrics(spark, name)
    old = result["before"]["snapshot"]
    result["retained_snapshot_rows"] = snapshot_rows(spark, name, old)
    if result["retained_snapshot_rows"] != result["before"]["rows"]:
        raise RuntimeError("retained snapshot changed")
    result["row_count_check"] = {
        "mode": "nondecreasing-with-live-appends"
        if config["live_writers"]
        else "exact",
        "scope": "snapshot row counts only; independent acknowledged-body reconciliation is required",
    }
    before, after = result["before"]["rows"], result["after"]["rows"]
    if after < before or (not config["live_writers"] and after != before):
        raise RuntimeError(
            "compaction changed the row count outside the declared writer mode"
        )


def run(output=None):
    if output is None:
        output = results_directory()
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
        report.update(
            table=name, target_file_bytes=target, configuration=configuration()
        )
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
