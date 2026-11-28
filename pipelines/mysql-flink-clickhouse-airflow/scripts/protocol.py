"""Prove the Kafka consumer's transaction visibility before recovery tests."""

import time
import traceback

from correctness import reconcile, settle
from lab import actor, app, case, events, fixture, phase, save, sql, wait


def protocol_control():
    if fixture()["stage"] not in ["ready", "tested", "failed"]:
        raise RuntimeError(
            "Initialize the source and pipeline before protocol controls"
        )
    run, path = case("read-committed-control")
    passed = False
    process, stream = None, None
    try:
        phase("baseline-fence")
        app("manifest", run, path)
        settle(path)
        reconcile(path)
        phase("transaction-isolation-control")
        process, stream = actor("probe-isolation", run, path)
        wait(
            lambda: any(
                r.get("event") == "probe-open"
                for r in events(path / "probe-isolation.jsonl")
            ),
            60,
        )
        began = time.monotonic()
        observations = 0
        while time.monotonic() - began < 5:
            rows = sql(
                f"SELECT count() AS n FROM pipeline.history WHERE topic='margins.isolation' AND JSONExtractString(payload,'id')='{run}'"
            )
            if int(rows[0]["n"]):
                raise AssertionError("Uncommitted transaction became query-visible")
            observations += 1
            time.sleep(0.25)
        (path / "abort-probe").touch()
        if process.wait(timeout=60) != 0:
            raise RuntimeError("Native transaction probe failed")
        for host in ["ch1", "ch2", "ch3"]:
            wait(
                lambda: sql(
                    f"SELECT groupUniqArray(JSONExtractString(payload,'state')) AS states FROM pipeline.history WHERE topic='margins.isolation' AND JSONExtractString(payload,'id')='{run}'",
                    host,
                )[0]["states"]
                == ["committed"],
                60,
            )
        save(
            path / "isolation.json",
            {
                "held_observations": observations,
                "hold_s": time.monotonic() - began,
                "aborted_visible": 0,
                "committed_visible_on_replicas": 3,
            },
        )
        passed = True
    except BaseException:
        (path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        (path / "abort-probe").touch()
        if process is not None and process.poll() is None:
            process.wait(timeout=60)
        if stream is not None:
            stream.close()
        save(
            path / "status.json",
            {
                "passed": passed,
                "kind": "read-committed-control",
                "fixture": fixture()["id"],
                "run": run,
            },
        )
