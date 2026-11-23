"""Native full-file Iceberg rewrite while example-owned application tasks stay live."""

import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, "/opt/lab")
import compact
from resources import Sampler


def body(spark, table, snapshot, seed_rows, maximum):
    sql = f"SELECT count(*) AS rows,count(DISTINCT event_id) AS unique_ids,min(event_id) AS first_id,max(event_id) AS last_id,sum(CASE WHEN event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR payload_sha256 IS NULL OR event_id<0 OR event_id>={maximum} OR customer_id!=event_id%10000 OR amount_cents!=event_id%10000+1 OR payload_sha256!=sha2(CAST(event_id AS STRING),256) THEN 1 ELSE 0 END) AS invalid_bodies FROM lake.lab.{table} VERSION AS OF {snapshot}"
    value = spark.sql(sql).first().asDict()
    if not (
        seed_rows <= value["rows"] <= maximum
        and value["rows"] == value["unique_ids"]
        and value["first_id"] == 0
        and value["invalid_bodies"] == 0
    ):
        raise RuntimeError("native pinned-snapshot body check failed: " + repr(value))
    return value


def main():
    root = Path("/results")
    config = json.loads((root / "concurrent-input.json").read_text())
    report = {"passed": False, "started_ns": time.time_ns()}
    sampler = Sampler(root / "concurrent-native-resources.jsonl")
    sampler.start()
    sampler.attach_parent_jvm()
    spark, failure = None, None
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
            raise RuntimeError("native maintenance version differs")
        table, rows = config["table"], config["seed_rows"]
        name = "lab." + table
        report["retained_before"] = body(
            spark, table, config["snapshot_id"], rows, rows
        )
        report["before"] = compact.metrics(spark, name)
        base_files = [
            f for f in report["before"]["file_membership"] if f["records"] == rows
        ]
        if len(base_files) != 1:
            raise RuntimeError("expected the witnessed two-million-row base file")
        report["rewrite_started_ns"] = time.time_ns()
        report["rewrite"] = [
            row.asDict()
            for row in spark.sql(
                f"CALL lake.system.rewrite_data_files(table=>'{name}',strategy=>'binpack',options=>map('rewrite-all','true','target-file-size-bytes','134217728','partial-progress.enabled','false','max-concurrent-file-group-rewrites','1'))"
            ).collect()
        ]
        report["rewrite_ended_ns"] = time.time_ns()
        compact.save_json(
            root / "rewrite-window.json",
            {key: report[key] for key in ("rewrite_started_ns", "rewrite_ended_ns")},
        )
        report["after"] = compact.metrics(spark, name)
        report["current_body"] = body(
            spark,
            table,
            report["after"]["snapshot"],
            rows,
            rows + config["maximum_append_rows"],
        )
        report["retained_after"] = body(spark, table, config["snapshot_id"], rows, rows)
        if report["retained_before"] != report["retained_after"]:
            raise RuntimeError("retained seed snapshot changed")
        if (
            not report["rewrite"]
            or sum(v["rewritten_bytes_count"] for v in report["rewrite"])
            < base_files[0]["bytes"]
            or any(v.get("failed_data_files_count", 0) for v in report["rewrite"])
        ):
            raise RuntimeError("full baseline rewrite not established")
        if base_files[0]["location"] in {
            f["location"] for f in report["after"]["file_membership"]
        }:
            raise RuntimeError("original million-row file is still live after rewrite")
    except BaseException as error:
        failure = error
        report.update(error=repr(error), traceback=traceback.format_exc())
    finally:
        if spark is not None:
            try:
                spark.stop()
            except BaseException as error:
                failure = failure or error
                report["stop_error"] = repr(error)
        try:
            report["resources"] = sampler.close()
        except BaseException as error:
            failure = failure or error
            report["resource_error"] = repr(error)
        report.update(passed=failure is None, completed_ns=time.time_ns())
        compact.save_json(root / "concurrent-native-result.json", report)
    if failure:
        raise failure


if __name__ == "__main__":
    main()
