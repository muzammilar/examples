"""Run each fault serially with live, externally witnessed source applications."""

import traceback

from correctness import allow_writes, reconcile, settle
from faults import acks, backfill, pause_resume, worker
from lab import (
    ROOT,
    actor,
    app,
    case,
    command,
    events,
    fixture,
    owned,
    phase,
    save,
    wait,
)
from lifecycle import start_dag, wait_dag
from observations import disk_usage, resources


def run_case(kind):
    operations = {
        "connector-lifecycle": pause_resume,
        "backfill-recovery": backfill,
        "worker-recovery": worker,
    }
    if kind not in operations:
        raise ValueError(kind)
    state = fixture()
    if state["stage"] not in ["ready", "tested", "failed"]:
        raise RuntimeError("An initialized, idle fixture is required")
    run, path = case(kind)
    passed, actors = False, []
    state["stage"] = "testing"
    save(ROOT / "results/fixture.json", state)

    def live_wait(predicate, timeout=120):
        def guarded():
            for role, process, _ in actors:
                if process.poll() is not None:
                    raise AssertionError(
                        role + " stopped early; inspect its preserved log"
                    )
            return predicate()

        wait(guarded, timeout)

    try:
        phase(kind + "-preflight")
        app("manifest", run, path)
        # Reconcile retained histories first, including any previous failed attempt.
        # A later run never retroactively changes that attempt's failed status.
        settle(path)
        reconcile(path)
        allow_writes()
        phase(kind + "-baseline")
        for role in ["writer", "reader"]:
            process, stream = actor(role, run, path)
            actors.append((role, process, stream))
        live_wait(lambda: len(acks(path)) >= 5, 60)
        live_wait(
            lambda: any(
                r.get("event") == "read" and r.get("result") == "ok"
                for r in events(path / "reader.jsonl")
            ),
            60,
        )
        disk_usage(path, "baseline")
        operations[kind](path, live_wait)
        live_wait(lambda: True, 2)
        phase(kind + "-source-fence")
        (path / "stop").touch()
        writer = next(process for role, process, _ in actors if role == "writer")
        if writer.wait(timeout=90) != 0:
            raise RuntimeError("Source writer failed; do not discard its witness")
        settle(path)
        reconcile(path)
        dag, dag_path = case("airflow-reconciliation")
        start_dag("margin_reconciliation", dag, dag_path)
        wait_dag("margin_reconciliation", dag, dag_path)
        save(
            path / "reconciliation-dag.json",
            {
                "run": dag,
                "evidence": str(dag_path.relative_to(ROOT)),
                "state": "success",
            },
        )
        (path / "stop-reader").touch()
        reader = next(process for role, process, _ in actors if role == "reader")
        if reader.wait(timeout=100) != 0:
            raise RuntimeError(
                "Reader reported a wrong result or unavailable historical data"
            )
        resources(path)
        passed = True
    except BaseException:
        (path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        (path / "stop").touch()
        (path / "stop-reader").touch()
        stopped = {}
        cleanup_errors = []
        for role, process, stream in actors:
            try:
                stopped[role] = process.wait(timeout=100)
            except Exception as error:
                stopped[role] = "stop-unconfirmed"
                cleanup_errors.append(role + ": " + str(error))
            finally:
                stream.close()
        passed = passed and not cleanup_errors
        state["stage"] = "tested" if passed else "failed"
        save(ROOT / "results/fixture.json", state)
        save(
            path / "status.json",
            {
                "passed": passed,
                "fixture": state["id"],
                "run": run,
                "kind": kind,
                "actor_exit_codes": stopped,
                "cleanup_errors": cleanup_errors,
            },
        )
        # Restore only the components this example's tests may have killed.
        for role in ["connect", "taskmanager1"]:
            if not owned(role)["State"]["Running"]:
                command("start", role)

    if not passed:
        raise RuntimeError(
            "Case cleanup did not complete; retain and diagnose its fixture"
        )
