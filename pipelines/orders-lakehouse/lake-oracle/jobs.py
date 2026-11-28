"""Native Spark snapshot read and rewrite procedure, not an alternative writer."""

import json
from pathlib import Path
import sys
import time
from pyspark.sql import SparkSession

builder = (
    SparkSession.builder.appName("orders-lakehouse-" + sys.argv[1])
    .config(
        "spark.sql.extensions",
        "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    )
    .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.eventLog.enabled", "true")
    .config("spark.eventLog.dir", "file:///data/events")
    .config("spark.eventLog.compress", "false")
    .config("spark.eventLog.rolling.enabled", "false")
)
props = json.loads(Path("/app/catalog.json").read_text())
if props["s3.endpoint"] != "http://s3:8333" or props["warehouse"] != "s3://warehouse/":
    raise ValueError("Non-lab catalog")
props["jdbc.ApplicationName"] = "lake-spark"
for key, value in props.items():
    builder = builder.config("spark.sql.catalog.lake." + key, value)
spark = builder.getOrCreate()
started = time.monotonic()
try:
    print(
        json.dumps(
            dict(
                native_spark=spark.version,
                native_iceberg=spark._jvm.org.apache.iceberg.IcebergBuild.fullVersion(),
                application_id=spark.sparkContext.applicationId,
                python=sys.version,
            )
        ),
        flush=True,
    )
    if sys.argv[1] == "compact":
        result = spark.sql(
            """CALL lake.system.rewrite_data_files(
            table => 'lab.orders', strategy => 'binpack',
            options => map('target-file-size-bytes','134217728',
                           'min-input-files','2', 'max-concurrent-file-group-rewrites','1'))"""
        )
        print(
            json.dumps(dict(compaction=[r.asDict() for r in result.collect()])),
            flush=True,
        )
    elif sys.argv[1] == "verify":
        snapshot = int(sys.argv[2])
        rows = spark.sql(
            f"""SELECT phase,count(*) AS rows,count(DISTINCT order_id) AS distinct_ids,
                       sum(amount_cents) AS cents,count(DISTINCT customer_id) AS customers
                       FROM lake.lab.orders VERSION AS OF {snapshot} GROUP BY phase ORDER BY phase"""
        ).collect()
        print(
            json.dumps(
                dict(snapshot=snapshot, aggregates=[r.asDict() for r in rows]),
                default=str,
            ),
            flush=True,
        )
    else:
        raise ValueError("Expected compact or verify")
    print(
        json.dumps(dict(passed=True, spark_work_s=time.monotonic() - started)),
        flush=True,
    )
finally:
    spark.stop()
    print(
        json.dumps(
            dict(
                actor_counters={
                    n: (Path("/sys/fs/cgroup") / n).read_text()
                    for n in (
                        "cpu.stat",
                        "memory.current",
                        "memory.peak",
                        "memory.events",
                        "io.stat",
                    )
                }
            )
        ),
        flush=True,
    )
