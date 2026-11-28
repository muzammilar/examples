"""Bounded connector, backfill and TaskManager cases with live source actors."""

import json
import subprocess
import time

from lab import (
    ROOT,
    api,
    app,
    case,
    command,
    events,
    flink,
    native,
    owned,
    phase,
    save,
    sql,
)
from lifecycle import metadata, start_dag, wait_dag
from observations import disk_usage


def acks(path):
    return [r for r in events(path / "writer.jsonl") if r.get("event") == "ack"]


def live_reads(path):
    return [
        r
        for r in events(path / "reader.jsonl")
        if r.get("event") == "recent-read" and r.get("result") == "visible"
    ]


def connector_running():
    status = api("/connectors/margins/status")
    return (
        status["connector"]["state"] == "RUNNING"
        and status["tasks"]
        and all(t["state"] == "RUNNING" for t in status["tasks"])
    )


def pause_resume(path, live_wait):
    native(path, "before-pause")
    phase("connector-pause")
    api("/connectors/margins/pause", "PUT")
    try:
        live_wait(
            lambda: api("/connectors/margins/status")["connector"]["state"] == "PAUSED",
            60,
        )
        before = len(acks(path))
        began = time.monotonic()
        while time.monotonic() - began < 10:
            live_wait(lambda: True, 2)
            time.sleep(0.5)
        after = len(acks(path))
        if after < before + 5:
            raise AssertionError("Source writer stalled during a paused connector")
        save(
            path / "pause.json",
            {
                "hold_s": time.monotonic() - began,
                "acks_before": before,
                "acks_while_paused": after,
                "status": api("/connectors/margins/status"),
            },
        )
        disk_usage(path, "paused")
    finally:
        api("/connectors/margins/resume", "PUT")
    phase("connector-resume")
    began = time.monotonic()
    live_wait(connector_running, 180)
    observed = len(live_reads(path))
    live_wait(lambda: len(live_reads(path)) > observed, 180)
    save(
        path / "resume.json",
        {"running_and_new_sample_visible_s": time.monotonic() - began},
    )
    native(path, "after-resume")


def crash(role, path, live_wait, recovered):
    target = owned(role)
    before = len(acks(path))
    began = time.monotonic()
    save(path / (role + "-before.json"), target)
    subprocess.run(
        ["docker", "kill", "--signal", "KILL", target["Id"]],
        check=True,
        capture_output=True,
        text=True,
    )
    stopped = owned(role)
    save(path / (role + "-killed.json"), stopped)
    if (
        stopped["State"]["Running"]
        or stopped["State"]["ExitCode"] != 137
        or stopped["State"]["OOMKilled"]
    ):
        raise AssertionError("Requested SIGKILL not proved")
    try:
        disk_usage(path, "held-" + role)
        while time.monotonic() - began < 10:
            live_wait(lambda: True, 2)
            time.sleep(0.5)
        if len(acks(path)) < before + 5:
            raise AssertionError("Source writes did not continue during component loss")
        save(
            path / (role + "-held.json"),
            {
                "hold_s": time.monotonic() - began,
                "acks_before": before,
                "acks_while_down": len(acks(path)),
            },
        )
    finally:
        command("start", role)
    restart = time.monotonic()
    live_wait(recovered, 180)
    at = len(live_reads(path))
    live_wait(lambda: len(live_reads(path)) > at, 180)
    save(
        path / (role + "-recovery.json"),
        {
            "restart_to_running_and_recent_visible_s": time.monotonic() - restart,
            "kill_to_recovered_s": time.monotonic() - began,
            "same_container_id": owned(role)["Id"] == target["Id"],
        },
    )


def worker(path, live_wait):
    native(path, "before-worker-kill")
    job = json.loads((ROOT / "results/job.json").read_text())["id"]
    old = flink("/jobs/" + job + "/checkpoints")["latest"]["completed"]["id"]
    phase("worker-loss")

    def restored():
        graph = flink("/jobs/" + job)
        return (
            graph["state"] == "RUNNING"
            and all(v["status"] == "RUNNING" for v in graph["vertices"])
            and flink("/jobs/" + job + "/checkpoints")["latest"]["completed"]["id"]
            > old
        )

    crash("taskmanager1", path, live_wait, restored)
    phase("worker-recovered")
    native(path, "after-worker-recovery")


def backfill(path, live_wait):
    phase("schema-change")
    from lab import mysql

    if (
        mysql(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema='orders' AND table_name='orders' AND column_name='shipping_cents'"
        )
        == "0"
    ):
        began = time.monotonic()
        mysql(
            "ALTER TABLE orders ADD COLUMN shipping_cents BIGINT NOT NULL DEFAULT 0, ALGORITHM=INSTANT"
        )
        save(
            path / "schema-change.json",
            {
                "ddl": "ADD shipping_cents BIGINT NOT NULL DEFAULT 0, ALGORITHM=INSTANT",
                "duration_s": time.monotonic() - began,
            },
        )
    correction, correction_path = case("shipping-correction")
    app("correct", correction, correction_path)
    signal, signal_path = case("incremental-backfill")
    incremental_before = int(
        sql(
            "SELECT count() AS n FROM pipeline.history WHERE topic='cdc.orders.orders' AND JSONExtractString(payload,'op')='r' AND JSONExtractString(payload,'source','snapshot')='incremental'"
        )[0]["n"]
    )
    start_dag("margin_backfill", signal, signal_path)
    phase("incremental-snapshot")

    def incremental_active():
        if (
            metadata(
                f"SELECT state FROM dag_run WHERE dag_id='margin_backfill' AND run_id='{signal}'"
            )
            == "failed"
        ):
            raise AssertionError(
                "Backfill DAG failed before an active snapshot; inspect task logs"
            )
        completed = sql(
            f"SELECT count() AS n FROM pipeline.history WHERE topic='cdc.notifications' AND JSONExtractString(payload,'id')='{signal}' AND JSONExtractString(payload,'type')='COMPLETED'"
        )[0]["n"]
        if int(completed):
            raise AssertionError(
                "Missed active snapshot window; do not claim an interrupted snapshot"
            )
        return (
            int(
                sql(
                    "SELECT count() AS n FROM pipeline.history WHERE topic='cdc.orders.orders' AND JSONExtractString(payload,'op')='r' AND JSONExtractString(payload,'source','snapshot')='incremental'"
                )[0]["n"]
            )
            >= incremental_before + 1024
        )

    live_wait(incremental_active, 180)
    initial_reads = int(
        sql(
            "SELECT uniqExactIf(tuple(partition,offset),JSONExtractString(payload,'op')='r' AND JSONExtractString(payload,'source','snapshot')!='incremental') AS n FROM pipeline.history WHERE topic='cdc.orders.orders'"
        )[0]["n"]
    )
    native(path, "before-incremental-kill")
    phase("connect-loss-during-backfill")
    crash("connect", path, live_wait, connector_running)
    phase("backfill-resumed")
    wait_dag("margin_backfill", signal, signal_path)
    # A retried request must reuse its identity and leave exactly one source signal.
    app("request-backfill", signal, signal_path)
    from lab import mysql as source_sql

    if (
        source_sql(
            f"SELECT count(*) FROM debezium_signal WHERE id='{signal}' AND type='execute-snapshot'"
        )
        != "1"
    ):
        raise AssertionError("Backfill retry created another logical signal")
    save(
        path / "backfill.json",
        {
            "signal": signal,
            "evidence": str(signal_path.relative_to(ROOT)),
            "correction": correction,
            "request_rows_after_retry": 1,
        },
    )
    after_initial_reads = int(
        sql(
            "SELECT uniqExactIf(tuple(partition,offset),JSONExtractString(payload,'op')='r' AND JSONExtractString(payload,'source','snapshot')!='incremental') AS n FROM pipeline.history WHERE topic='cdc.orders.orders'"
        )[0]["n"]
    )
    save(
        path / "initial-snapshot-preserved.json",
        {"before": initial_reads, "after": after_initial_reads},
    )
    if after_initial_reads != initial_reads:
        raise AssertionError(
            "Initial snapshot records increased during retained incremental recovery"
        )
    native(path, "after-incremental-recovery")
