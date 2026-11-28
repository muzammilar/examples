"""Native Spark joins; publish only a verified, immutable HDFS directory."""

import json
import os
import sys
import time
from pyspark.sql import SparkSession, functions as F

BASE = "hdfs://namenode:9000/lab"
ROWS = int(os.environ.get("ORDER_COUNT", "1000000"))
CUSTOMERS = int(os.environ.get("CUSTOMER_COUNT", "10000"))


def expected_amount(n):
    cycles, tail = divmod(n, 1000)
    return 100 * n + cycles * 499500 + tail * (tail + 1) // 2


def verify(frame):
    bad = frame.where(
        """order_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL
        OR region IS NULL OR order_revision IS NULL OR customer_revision IS NULL
        OR event_second IS NULL OR customer_shard IS NULL
        OR customer_id != 1 + pmod(order_id - 1, {customers})
        OR amount_cents != 100 + pmod(order_id, 1000)
        OR region != concat('region-', cast(pmod(customer_id, 16) AS STRING))
        OR order_revision != 1 OR customer_revision != 1
        OR event_second != 1700000000 + pmod(order_id, 86400)
        OR customer_shard != pmod(customer_id, 3)""".format(customers=CUSTOMERS)
    ).count()
    totals = (
        frame.agg(
            F.count("*").alias("rows"),
            F.countDistinct("order_id").alias("ids"),
            F.min("order_id").alias("first"),
            F.max("order_id").alias("last"),
            F.sum("amount_cents").alias("amount"),
        )
        .first()
        .asDict()
    )
    expected = dict(
        rows=ROWS, ids=ROWS, first=1, last=ROWS, amount=expected_amount(ROWS)
    )
    if bad or totals != expected:
        raise ValueError(f"Invalid rows={bad}, totals={totals}, expected={expected}")
    return totals


def manifest(fs, path, stage, batch, totals):
    inventory = []
    files = fs.listFiles(path(stage), True)
    while files.hasNext():
        item = files.next()
        if item.getPath().getName().endswith(".parquet"):
            inventory.append(
                dict(
                    path=str(item.getPath()).split(stage, 1)[1],
                    bytes=item.getLen(),
                    checksum=str(fs.getFileChecksum(item.getPath())),
                )
            )
    value = dict(
        batch=batch,
        rows=ROWS,
        customers=CUSTOMERS,
        recipe="orders-v1-one-based",
        totals=totals,
        files=sorted(inventory, key=lambda x: x["path"]),
    )
    stream = fs.create(path(stage + "/_MANIFEST.json"), False)
    try:
        stream.write(bytearray(json.dumps(value, sort_keys=True).encode()))
        stream.hsync()
    finally:
        stream.close()


def main():
    if not 1 <= CUSTOMERS <= ROWS or ROWS > 10000000:
        raise ValueError("Require 1 <= customers <= rows <= 10M")
    mode, batch = sys.argv[1:]
    if mode not in ("seed", "join", "verify") or batch not in (
        "baseline",
        "executor",
        "datanode",
    ):
        raise ValueError("Unexpected mode/batch")
    spark = (
        SparkSession.builder.appName("hdfs-orders-" + batch)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.eventLog.enabled", "true")
        .config("spark.eventLog.dir", BASE + "/events")
        .config("spark.eventLog.compress", "false")
        .config("spark.eventLog.rolling.enabled", "false")
        .getOrCreate()
    )
    started = time.monotonic()
    jvm, conf = spark.sparkContext._jvm, spark.sparkContext._jsc.hadoopConfiguration()
    fs = jvm.org.apache.hadoop.fs.FileSystem.get(conf)
    path = jvm.org.apache.hadoop.fs.Path
    try:
        if mode == "seed":
            if fs.exists(path(BASE + "/input")):
                raise ValueError("Input exists; refusing to reseed")
            stage = BASE + "/input-stage"
            orders = spark.range(1, ROWS + 1, numPartitions=12).selectExpr(
                "id AS order_id",
                f"1 + pmod(id - 1, {CUSTOMERS}) AS customer_id",
                "100 + pmod(id, 1000) AS amount_cents",
                "cast(1 AS BIGINT) AS order_revision",
                "1700000000 + pmod(id, 86400) AS event_second",
            )
            customers = spark.range(1, CUSTOMERS + 1, numPartitions=3).selectExpr(
                "id AS customer_id",
                "concat('region-', cast(pmod(id, 16) AS STRING)) AS region",
                "cast(1 AS BIGINT) AS customer_revision",
            )
            orders = orders.withColumn(
                "event_day", F.to_date(F.timestamp_seconds("event_second"))
            )
            orders.write.mode("errorifexists").partitionBy("event_day").option(
                "compression", "zstd"
            ).parquet(stage + "/orders")
            customers.write.mode("errorifexists").option("compression", "zstd").parquet(
                stage + "/customers"
            )
            joined = (
                spark.read.parquet(stage + "/orders")
                .join(spark.read.parquet(stage + "/customers"), "customer_id")
                .withColumn("customer_shard", F.pmod("customer_id", F.lit(3)))
            )
            result = verify(joined)
            manifest(fs, path, stage, "input", result)
            if not fs.rename(path(stage), path(BASE + "/input")):
                raise ValueError("Input publication rename failed")
        else:
            output, stage = BASE + "/published/" + batch, BASE + "/staging/" + batch
            if mode == "join" and not fs.exists(path(output)):
                if not fs.exists(path(BASE + "/input/_MANIFEST.json")):
                    raise ValueError("Input publication manifest is missing")
                orders = spark.read.parquet(BASE + "/input/orders").drop("event_day")
                customers = spark.read.parquet(BASE + "/input/customers")
                # A real shuffle, not a broadcast-only dimension lookup.
                joined = (
                    orders.hint("merge")
                    .join(customers.hint("merge"), "customer_id")
                    .withColumn("customer_shard", F.pmod("customer_id", F.lit(3)))
                )
                spark.sparkContext.setJobGroup(
                    batch + "-publication-write", "Join and publish partitioned orders"
                )
                joined.write.mode("errorifexists").partitionBy("customer_shard").option(
                    "compression", "zstd"
                ).parquet(stage + "/orders")
                spark.sparkContext.setJobGroup("", "")
                verified = spark.read.parquet(stage + "/orders")
                result = verify(verified)
                verified.groupBy("region").agg(
                    F.count("*").alias("orders"),
                    F.sum("amount_cents").alias("amount_cents"),
                ).write.mode("errorifexists").option("compression", "zstd").parquet(
                    stage + "/regions"
                )
                manifest(fs, path, stage, batch, result)
                fs.mkdirs(path(BASE + "/published"))
                if not fs.rename(path(stage), path(output)):
                    raise ValueError("Output publication rename failed")
            if not fs.exists(path(output + "/_MANIFEST.json")):
                raise ValueError("Output has no publication manifest")
            result = verify(spark.read.parquet(output + "/orders"))
        print(
            "PIPELINE_RESULT "
            + json.dumps(
                dict(
                    mode=mode,
                    batch=batch,
                    totals=result,
                    wall_s=time.monotonic() - started,
                )
            ),
            flush=True,
        )
    finally:
        spark.stop()
        from pathlib import Path

        print(
            "ACTOR_COUNTERS "
            + json.dumps(
                {
                    name: (Path("/sys/fs/cgroup") / name).read_text()
                    for name in ("cpu.stat", "memory.peak", "io.stat")
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
