"""Bounded batch/query screens; comparison cases retain the existing body oracle."""

import json
import traceback
import uuid

from lab import (
    ROOT,
    BACKENDS,
    command,
    workload,
    save,
    phase,
    assert_source,
    select_backend,
    owned,
    disk_snapshot,
)
from lifecycle import is_retired


def current_fixture():
    choices = [
        p
        for p in (ROOT / "results/cases").glob("*/case.json")
        if json.loads(p.read_text())["kind"] == "healthy-common-otlp"
        and not is_retired(p)
        and json.loads((p.parent / "status.json").read_text())["passed"]
    ]
    if not choices:
        raise RuntimeError("A live qualified million-record OTLP fixture is required")
    return max(choices, key=lambda p: p.stat().st_mtime_ns).parent / "fixture.json"


def batch_screen(audit):
    from control import verify

    audit()
    base = json.loads(current_fixture().read_text())
    for batch in [1, 100, 1000, 10000]:
        run = uuid.uuid4().hex
        path = ROOT / "results/cases" / run
        fixture = dict(
            base,
            run=run,
            payload_seed=base.get("payload_seed") or base["run"],
            series=100,
            points=100,
            logs=10000,
        )
        save(path / "fixture.json", fixture)
        save(
            path / "case.json",
            {
                "kind": "batch-screen",
                "protocol": "otlp",
                "batch": batch,
                "run": run,
                "scope": "10k-record diagnostic screen, not sustained capacity",
            },
        )
        passed = False
        try:
            assert_source(path)
            for backend in BACKENDS:
                select_backend(backend, path)
                disk_snapshot(backend, path, "before-batch")
                for signal in ["metrics", "logs"]:
                    directory = path / backend / signal
                    directory.parent.mkdir(exist_ok=True)
                    phase(f"batch-screen:{batch}:{backend}:{signal}")
                    result = workload(
                        "--mode=push",
                        "--backend=" + backend,
                        "--signal=" + signal,
                        "--batch=" + str(batch),
                        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
                        "--output=/" + str(directory.relative_to(ROOT)),
                        check=False,
                    )
                    (directory / "push.log").write_text(
                        result.stdout + "\n" + result.stderr
                    )
                    if result.returncode:
                        raise RuntimeError(
                            "Batch screen failed; preserve journal: " + result.stderr
                        )
                    verify(backend, signal, directory, path / "fixture.json")
                disk_snapshot(backend, path, "after-batch")
            passed = True
        except BaseException:
            (path / "failure.txt").write_text(traceback.format_exc())
            raise
        finally:
            save(
                path / "status.json",
                {
                    "passed": passed,
                    "batch": batch,
                    "scope": "Bounded screen, not repeated/sustained performance",
                },
            )


def query_screen(audit):
    audit()
    fixture = current_fixture()
    path = ROOT / "results/queries" / uuid.uuid4().hex
    path.mkdir(parents=True)
    passed = False
    try:
        assert_source(path)
        for trial in range(3):
            order = (
                ["greptime", "victoria"] if trial % 2 == 0 else ["victoria", "greptime"]
            )
            for backend in order:
                select_backend(backend, path)
                for name in ["metric-count", "metric-sum", "log-count"]:
                    role = (
                        "greptime"
                        if backend == "greptime"
                        else ("vlogs" if name == "log-count" else "vmetrics")
                    )
                    before = owned(role)
                    phase(f"query-restart:{trial}:{backend}:{name}")
                    command("restart", role)
                    select_backend(backend, path)
                    after = owned(role)
                    if (
                        before["Id"] != after["Id"]
                        or before["State"]["StartedAt"] == after["State"]["StartedAt"]
                    ):
                        raise RuntimeError("Engine restart was not demonstrated")
                    destination = path / str(trial) / backend / name
                    destination.mkdir(parents=True)
                    save(
                        destination / "restart.json",
                        {
                            "same_container": True,
                            "started_at": after["State"]["StartedAt"],
                            "image_id": after["Image"],
                            "cache_scope": "engine/data-role restart; host filesystem cache retained",
                        },
                    )
                    phase(f"query-first-warm:{trial}:{backend}:{name}")
                    result = workload(
                        "--mode=query",
                        "--backend=" + backend,
                        "--query=" + name,
                        "--query-repeats=31",
                        "--fixture=/" + str(fixture.relative_to(ROOT)),
                        "--report=/"
                        + str((destination / "queries.json").relative_to(ROOT)),
                        check=False,
                    )
                    (destination / "tool.log").write_text(
                        result.stdout + "\n" + result.stderr
                    )
                    if result.returncode:
                        raise RuntimeError(
                            "Query/independent oracle failed: " + result.stderr
                        )
        passed = True
    except BaseException:
        (path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(
            path / "status.json",
            {
                "passed": passed,
                "trials": 3,
                "first_plus_warm_queries": 31,
                "fixture": str(fixture.relative_to(ROOT)),
                "scope": "Grouped exact-answer query latency; bounded diagnostic, not sustained query capacity or established p99 tail",
            },
        )
