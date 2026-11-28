"""Recompute an explicit historical snapshot; never assign a publication-time version."""

import json
import os
from pathlib import Path
import time
from pyspark.sql import SparkSession, functions as F

path = Path("/results/historical") / os.environ["CASE_ID"]
started = time.monotonic()
spark = SparkSession.builder.appName("versioned-historical-replay").getOrCreate()
spark.sparkContext.setLogLevel("WARN")
orders = (
    spark.read.schema(
        "id long,customer_id long,amount_cents long,revision long,cancelled boolean,event_second long"
    )
    .option("mode", "FAILFAST")
    .csv(str(path / "orders.csv"))
    .alias("o")
)
customers = (
    spark.read.schema("id long,region string,revision long")
    .option("mode", "FAILFAST")
    .csv(str(path / "customers.csv"))
    .alias("c")
)
result = (
    orders.join(customers, F.col("o.customer_id") == F.col("c.id"))
    .where(~F.col("o.cancelled"))
    .selectExpr(
        "o.id AS order_id",
        "o.customer_id",
        "o.amount_cents",
        "c.region",
        "o.revision AS order_revision",
        "c.revision AS customer_revision",
        "o.event_second",
        "o.revision * CAST(1000000 AS BIGINT) + c.revision AS version",
    )
)
result.orderBy("order_id").coalesce(1).write.mode("errorifexists").option(
    "compression", "none"
).csv(str(path / "spark-output"))
print(
    json.dumps(
        {
            "event": "spark_complete",
            "spark_version": spark.version,
            "duration_ms": (time.monotonic() - started) * 1000,
            "scope": "single bounded batch worker; not a distributed Spark capacity comparison",
        }
    ),
    flush=True,
)
spark.stop()
