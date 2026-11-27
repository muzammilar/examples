"""Write joined orders, then verify a separately reopened Parquet dataset."""
import json
import hashlib
from pathlib import Path
import os
import sys
from pyspark.sql import SparkSession, functions as F


OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "file:///data/orders")
EVENT_LOG_DIR = os.environ.get("EVENT_LOG_DIR", "file:///data/events")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("write", "verify"):
        raise ValueError("Expected write or verify")
    if OUTPUT_PATH not in ("file:///data/orders", "hdfs://namenode:9000/lab/orders"):
        raise ValueError("Unexpected output endpoint; this workload is local-only")
    if EVENT_LOG_DIR not in ("file:///data/events", "hdfs://namenode:9000/lab/events"):
        raise ValueError("Unexpected event-log endpoint; this workload is local-only")
    print("WORKLOAD_SOURCE_SHA256 " + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), flush=True)
    spark = (SparkSession.builder.appName("verified-orders")
             .config("spark.eventLog.enabled", "true")
             .config("spark.eventLog.dir", EVENT_LOG_DIR)
             .config("spark.eventLog.compress", "false")
             .config("spark.eventLog.rolling.enabled", "false")
             .getOrCreate())
    try:
        if sys.argv[1] == "write":
            customers = spark.range(10000).selectExpr("id AS customer_id", "id % 10 AS region")
            orders = spark.range(1000000, numPartitions=16).selectExpr(
                "id AS order_id", "id % 10000 AS customer_id",
                "100 + id % 1000 AS amount_cents",
            )
            joined = orders.join(customers, "customer_id")
            joined.write.mode("errorifexists").parquet(OUTPUT_PATH)
        actual = spark.read.parquet(OUTPUT_PATH)
        invalid = actual.where(
            (F.col("customer_id") != F.col("order_id") % 10000)
            | (F.col("amount_cents") != 100 + F.col("order_id") % 1000)
            | (F.col("region") != F.col("customer_id") % 10)
            | F.col("order_id").isNull()
            | F.col("customer_id").isNull()
            | F.col("amount_cents").isNull()
            | F.col("region").isNull()
        ).count()
        totals = actual.agg(
            F.count("*").alias("rows"),
            F.countDistinct("order_id").alias("unique_orders"),
            F.min("order_id").alias("min_id"),
            F.max("order_id").alias("max_id"),
            F.sum("amount_cents").alias("amount_cents"),
        ).first().asDict()
        expected = {"rows": 1000000, "unique_orders": 1000000, "min_id": 0,
                    "max_id": 999999, "amount_cents": 599500000}
        if totals != expected or invalid:
            raise ValueError(f"Wrong output {totals}, invalid bodies {invalid}")
        print("VERIFIED_RESULT " + json.dumps(totals))
    finally:
        spark.stop()
        counters = {}
        for name in ("cpu.stat", "memory.peak", "io.stat"):
            try:
                counters[name] = (Path("/sys/fs/cgroup") / name).read_text()
            except OSError as error:
                counters[name] = {"unavailable": str(error)}
        print("WORKLOAD_RESOURCE_RESULT " + json.dumps(counters), flush=True)


if __name__ == "__main__":
    main()
