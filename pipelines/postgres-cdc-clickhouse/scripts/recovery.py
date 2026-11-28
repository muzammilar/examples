"""Live snapshot/WAL overlap and retained connector crash recovery."""

import json
import subprocess
import time
import uuid
from lab import (
    ROOT,
    actor,
    acks,
    api,
    command,
    events,
    fixture,
    native,
    owned,
    pg,
    phase,
    save,
    snapshot_counts,
    wait,
)
from correctness import offset_fence, reconcile
from observations import resources


def test(path):
    value = fixture()
    if value["stage"] != "ready":
        raise RuntimeError(
            "Fresh ready fixture required; use retained verification/recovery"
        )
    value["stage"] = "testing"
    save(ROOT / "results/fixture.json", value)
    run = uuid.uuid4().hex
    save(
        ROOT / "results/cases" / run / "case.json", {"fixture": value["id"], "run": run}
    )
    (ROOT / "results/snapshot-complete").unlink(missing_ok=True)
    actors = []
    passed = False
    try:
        phase("before-snapshot")
        for role in ["writer", "reader"]:
            actors.append((role, *actor(role, run, path)))
        wait(lambda: len(acks(path)) >= 5, 60)
        api(
            "/connectors",
            "POST",
            {
                "name": "handoff",
                "config": json.loads((ROOT / "config/connector.json").read_text()),
            },
        )
        wait(lambda: snapshot_counts()["reads"] > 0, 180)
        initial = snapshot_counts()
        if initial["last"] != 0:
            raise RuntimeError("Snapshot already finished; overlap was not established")
        start_acks = len(acks(path))
        phase("snapshot-overlap")
        save(
            path / "snapshot-overlap-start.json",
            {"counts": initial, "source_acks": start_acks, "utc_ns": time.time_ns()},
        )
        wait(
            lambda: len(acks(path)) >= start_acks + 5
            and snapshot_counts()["last"] == 0,
            90,
        )
        save(
            path / "snapshot-live-progress.json",
            {
                "counts": snapshot_counts(),
                "source_acks": len(acks(path)),
                "utc_ns": time.time_ns(),
            },
        )
        wait(
            lambda: snapshot_counts()["last"] > 0 and snapshot_counts()["changes"] > 0,
            300,
        )
        (ROOT / "results/snapshot-complete").touch()
        phase("streaming")
        wait(
            lambda: any(r.get("result") == "ok" for r in events(path / "reader.jsonl")),
            60,
        )
        wait(lambda: snapshot_counts()["changes"] >= 50, 60)
        before = native(path, "before-connect-kill")
        before["snapshot_counts"] = snapshot_counts()
        save(path / "before-connect-kill.json", before)
        save(path / "source-offsets-before.json", api("/connectors/handoff/offsets"))
        target = owned("connect")
        save(path / "connect-before.json", target)
        baseline = len(acks(path))
        phase("connect-down")
        began = time.monotonic()
        subprocess.run(
            ["docker", "kill", "--signal", "KILL", target["Id"]],
            check=True,
            capture_output=True,
            text=True,
        )
        killed = owned("connect")
        save(path / "connect-killed.json", killed)
        if (
            killed["State"]["Running"]
            or killed["State"]["ExitCode"] != 137
            or killed["State"]["OOMKilled"]
        ):
            raise RuntimeError("SIGKILL not proved")
        wait(
            lambda: json.loads(
                pg(
                    "SELECT coalesce(json_agg(active),'[]') FROM pg_replication_slots WHERE slot_name='handoff'"
                )
            )
            == [False],
            30,
        )
        time.sleep(12)
        if len(acks(path)) < baseline + 5:
            raise RuntimeError("Source writer did not progress during connector loss")
        save(
            path / "held.json",
            {
                "hold_s": time.monotonic() - began,
                "acks_before": baseline,
                "acks_while_down": len(acks(path)),
                "slot": json.loads(
                    pg(
                        "SELECT json_agg(json_build_object('slot_name',slot_name,'active',active,'confirmed_flush_lsn',confirmed_flush_lsn)) FROM pg_replication_slots"
                    )
                ),
            },
        )
        phase("connect-recovery")
        restart_began = time.monotonic()
        command("start", "connect")
        wait(
            lambda: api("/connectors/handoff/status")["connector"]["state"]
            == "RUNNING",
            180,
        )
        wait(
            lambda: any(
                t["state"] == "RUNNING"
                for t in api("/connectors/handoff/status")["tasks"]
            ),
            180,
        )
        save(
            path / "recovery-timing.json",
            {
                "restart_to_task_running_s": time.monotonic() - restart_began,
                "kill_to_task_running_s": time.monotonic() - began,
            },
        )
        after = native(path, "after-connect-restart")
        if before["slot"][0]["slot_name"] != after["slot"][0]["slot_name"]:
            raise RuntimeError("Slot changed during recovery")
        phase("after")
        at = len(acks(path))
        wait(lambda: len(acks(path)) >= at + 10, 30)
        (ROOT / "results/cases" / run / "stop").touch()
        for role, child, stream in actors:
            if role == "writer":
                if child.wait(timeout=30) != 0:
                    raise RuntimeError("Writer failed")
                stream.flush()
        pg("REVOKE INSERT, UPDATE, DELETE ON orders, customers, receipts FROM app")
        offset_fence(path)
        save(path / "source-offsets-after.json", api("/connectors/handoff/offsets"))
        after_counts = snapshot_counts()
        save(path / "snapshot-counts-after.json", after_counts)
        if after_counts["reads"] != before["snapshot_counts"]["reads"]:
            raise RuntimeError("Unexpected new snapshot after retained restart")
        reconcile(path)
        (ROOT / "results/cases" / run / "stop-reader").touch()
        for role, child, stream in actors:
            if child.wait(timeout=30) != 0:
                raise RuntimeError(role + " failed")
        resources(path)
        passed = True
    finally:
        for role, child, stream in actors:
            (
                ROOT
                / "results/cases"
                / run
                / ("stop" if role == "writer" else "stop-reader")
            ).touch()
            if child.poll() is None:
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=10)
            stream.close()
        # Restore only the crashed owned connector; never reset its topic/slot.
        if not owned("connect")["State"]["Running"]:
            command("start", "connect")
        value["stage"] = "tested" if passed else "failed"
        save(ROOT / "results/fixture.json", value)
        save(
            path / "status.json", {"passed": passed, "fixture": value["id"], "run": run}
        )
