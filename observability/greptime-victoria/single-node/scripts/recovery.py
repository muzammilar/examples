"""Keep writer/readers live; recover the same owned process/storage and reconcile."""

from datetime import datetime
import json
import os
import subprocess
import time
import traceback
import uuid

from lab import (
    ROOT,
    PROJECT,
    BACKENDS,
    command,
    workload,
    owned,
    save,
    phase,
    assert_source,
    select_backend,
    disk_snapshot,
    native_cgroups,
    running_actors,
)
from lifecycle import is_retired


def events(path):
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raw = raw.rsplit(b"\n", 1)[0] if b"\n" in raw else b""
    return [json.loads(line) for line in raw.splitlines() if line]


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def inspect_actor(identifier, role):
    row = json.loads(
        subprocess.check_output(["docker", "inspect", identifier], text=True)
    )[0]
    labels = row["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != role
    ):
        raise RuntimeError("Foreign live actor")
    return row


def wait(predicate, timeout, actors):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for identifier, role, _ in actors:
            row = inspect_actor(identifier, role)
            if not row["State"]["Running"]:
                raise RuntimeError("Live actor stopped early: " + row["Name"])
        if predicate():
            return
        time.sleep(0.5)
    raise TimeoutError("Live phase deadline exceeded: " + predicate.__name__)


def acknowledged(path):
    return [
        r
        for r in events(path / "requests.jsonl")
        if r["event"] == "response" and r["outcome"] == "accepted"
    ]


def application_ready(path):
    return len(acknowledged(path)) >= 5 and all(
        any(
            r["kind"] == kind and r["result"] == "visible"
            for r in events(path / "reads.jsonl")
        )
        for kind in ["old", "recent"]
    )


def history_readback(backend, signal, fixture, directory, report):
    report.parent.mkdir(parents=True, exist_ok=True)
    result = workload(
        "--mode=verify-history",
        "--backend=" + backend,
        "--signal=" + signal,
        "--fixture=/" + str(fixture.relative_to(ROOT)),
        "--output=/" + str(directory.relative_to(ROOT)),
        "--report=/" + str(report.relative_to(ROOT)),
        check=False,
    )
    report.with_suffix(".log").write_text(result.stdout + "\n" + result.stderr)
    if result.returncode:
        raise RuntimeError("History/body oracle failed: " + result.stderr)
    value = json.loads(report.read_text())
    if not value["complete"] or value["result"]["unexpected_ids"]:
        raise RuntimeError("Incomplete or unexpected readback")
    return value


def verify_live_case(case, audit):
    fixture = case.parent / "fixture.json"
    for backend in BACKENDS:
        directories = [
            (signal, case.parent / backend / signal) for signal in ["metrics", "logs"]
        ]
        directories = [
            (signal, path)
            for signal, path in directories
            if (path / "requests.jsonl").exists()
        ]
        if not directories:
            continue
        select_backend(backend, audit)
        for signal, directory in directories:
            phase("retained-live:" + backend + ":" + signal)
            value = history_readback(
                backend,
                signal,
                fixture,
                directory,
                audit / case.parent.name / backend / signal / "history.json",
            )
            if value["result"]["missing_acknowledged_ids"]:
                raise RuntimeError(
                    "Acknowledged records remain missing; explicit recovery is required"
                )


def run_case(kind, audit):
    if kind not in ["live-control", "crash-metrics", "crash-logs"]:
        raise ValueError(kind)
    audit()
    candidates = [
        p
        for p in (ROOT / "results/cases").glob("*/case.json")
        if json.loads(p.read_text())["kind"] == "healthy-common-otlp"
        and not is_retired(p)
        and json.loads((p.parent / "status.json").read_text())["passed"]
    ]
    if not candidates:
        raise RuntimeError("Run make correctness before live recovery")
    prior = max(candidates, key=lambda p: p.stat().st_mtime_ns).parent / "fixture.json"
    data = json.loads(prior.read_text())
    run = uuid.uuid4().hex
    data.update(
        run=run,
        payload_seed=data.get("payload_seed") or data["run"],
        points=200,
        logs=2000000,
    )
    path = ROOT / "results/cases" / run
    save(path / "fixture.json", data)
    save(
        path / "case.json",
        {
            "kind": "live-recovery",
            "operation": kind,
            "run": run,
            "protocol": "otlp",
            "prior": str(prior.relative_to(ROOT)),
            "contract": "Synchronous Greptime ACKs must survive; Victoria admission may precede periodic persistence. All mismatches fail; missing ACKs are quantified before any recovery.",
        },
    )
    passed = False
    try:
        assert_source(path)
        for backend in BACKENDS:
            select_backend(backend, path)
            disk_snapshot(backend, path, "before-actors")
            native_cgroups(backend, path, "before-actors")
            actors = []
            role = (
                "greptime"
                if backend == "greptime"
                else ("vmetrics" if kind == "crash-metrics" else "vlogs")
            )
            target = None
            try:
                phase("live-baseline:" + backend)
                for signal in ["metrics", "logs"]:
                    directory = path / backend / signal
                    directory.mkdir(parents=True)
                    actor_role = signal + "-writer-reader"
                    name = PROJECT + "-" + run + "-" + backend + "-" + signal
                    identifier = (
                        command(
                            "run",
                            "--detach",
                            "--name",
                            name,
                            "--no-deps",
                            actor_role,
                            "--mode=live",
                            "--backend=" + backend,
                            "--signal=" + signal,
                            "--fixture=/"
                            + str((path / "fixture.json").relative_to(ROOT)),
                            "--prior-fixture=/" + str(prior.relative_to(ROOT)),
                            "--output=/" + str(directory.relative_to(ROOT)),
                        )
                        .stdout.strip()
                        .splitlines()[-1]
                    )
                    row = inspect_actor(identifier, actor_role)
                    if (
                        row["HostConfig"]["NanoCpus"] != 500000000
                        or row["HostConfig"]["Memory"] != 256 * 2**20
                    ):
                        raise RuntimeError("Live actor budget differs")
                    actors.append((identifier, actor_role, directory))
                wait(
                    lambda: all(application_ready(d) for _, _, d in actors), 45, actors
                )
                if kind == "live-control":
                    start = time.monotonic()
                    while time.monotonic() - start < 10:
                        wait(lambda: True, 2, actors)
                        time.sleep(0.5)
                else:
                    target = owned(role)
                    save(
                        path / (backend + "-before-kill.json"),
                        {"role": role, "container": target},
                    )
                    phase("fault:" + backend + ":" + role)
                    began = time.monotonic()
                    subprocess.run(
                        ["docker", "kill", "--signal", "KILL", target["Id"]],
                        check=True,
                        capture_output=True,
                    )
                    while owned(role)["State"]["Running"]:
                        time.sleep(0.1)
                    stopped = owned(role)
                    if (
                        stopped["State"]["ExitCode"] != 137
                        or stopped["State"]["OOMKilled"]
                    ):
                        raise RuntimeError("SIGKILL proof differs from requested fault")
                    save(path / (backend + "-killed.json"), stopped)
                    while time.monotonic() - began < 10:
                        wait(lambda: True, 2, actors)
                        time.sleep(0.5)
                    held = {
                        signal: {
                            "accepted_requests": len(
                                acknowledged(path / backend / signal)
                            ),
                            "unacknowledged_requests": sum(
                                r["event"] == "response" and r["outcome"] != "accepted"
                                for r in events(
                                    path / backend / signal / "requests.jsonl"
                                )
                            ),
                        }
                        for signal in ["metrics", "logs"]
                    }
                    save(
                        path / (backend + "-held.json"),
                        {"hold_s": time.monotonic() - began, "signals": held},
                    )
                    phase("recovery:" + backend + ":" + role)
                    subprocess.run(
                        ["docker", "start", target["Id"]],
                        check=True,
                        capture_output=True,
                    )
                    restarted = time.monotonic()
                    started = owned(role)["State"]["StartedAt"]

                    def recovered():
                        return all(
                            any(
                                r["kind"] == "recent"
                                and r["result"] == "visible"
                                and stamp(r["ack_completed_at"]) > stamp(started)
                                for r in events(directory / "reads.jsonl")
                            )
                            for _, _, directory in actors
                        )

                    wait(recovered, 120, actors)
                    save(
                        path / (backend + "-recovery.json"),
                        {
                            "kill_to_fresh_ack_visible_s": time.monotonic() - began,
                            "restart_to_fresh_ack_visible_s": time.monotonic()
                            - restarted,
                            "started_at": started,
                            "same_container": owned(role)["Id"] == target["Id"],
                        },
                    )
            finally:
                if target is not None and not owned(role)["State"]["Running"]:
                    subprocess.run(
                        ["docker", "start", target["Id"]],
                        check=True,
                        capture_output=True,
                    )
                for _, _, directory in actors:
                    (directory / "stop").touch()
                actor_errors = []
                for identifier, actor_role, directory in actors:
                    ended = subprocess.run(
                        ["docker", "wait", identifier],
                        text=True,
                        capture_output=True,
                        timeout=65,
                    )
                    row = inspect_actor(identifier, actor_role)
                    (directory / "actor.log").write_text(
                        subprocess.check_output(
                            ["docker", "logs", identifier],
                            text=True,
                            stderr=subprocess.STDOUT,
                        )
                    )
                    save(directory / "actor-exit.json", row)
                    if ended.returncode or row["State"]["ExitCode"] != 0:
                        actor_errors.append(str(directory))
                        continue
                    subprocess.run(
                        ["docker", "rm", identifier], check=True, capture_output=True
                    )
                if actor_errors:
                    raise RuntimeError(
                        "Live actors failed; witnesses retained: "
                        + ", ".join(actor_errors)
                    )
            phase("fenced-readback:" + backend)
            # No actor remains in flight. Query convergence is separate from durability.
            time.sleep(6)
            for signal in ["metrics", "logs"]:
                value = history_readback(
                    backend,
                    signal,
                    path / "fixture.json",
                    path / backend / signal,
                    path / backend / signal / "reconciliation-before.json",
                )
                if value["result"]["missing_acknowledged_ids"]:
                    if backend != "victoria" or kind != "crash-" + signal:
                        raise RuntimeError(
                            "Unexpected acknowledged loss; preserve before recovery"
                        )
                    replay_known_loss(path, signal)
            native_cgroups(backend, path, "after-readback")
            disk_snapshot(backend, path, "after-actors")
        passed = True
    except BaseException:
        (path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(
            path / "status.json",
            {
                "passed": passed,
                "run": run,
                "kind": "live-recovery",
                "operation": kind,
                "scope": "Live correctness/recovery diagnostic, not a throughput comparison",
            },
        )


def recover_witness():
    """Recover only proven old-process ACK loss from this lab's durable witness."""
    run = os.environ.get("CASE", "")
    if len(run) != 32 or any(c not in "0123456789abcdef" for c in run):
        raise ValueError("CASE must be the preserved case's 32-character run ID")
    if running_actors():
        raise RuntimeError("Freeze all live actors before witnessed recovery")
    path = ROOT / "results/cases" / run
    case = json.loads((path / "case.json").read_text())
    if case["kind"] != "live-recovery":
        raise RuntimeError("Not a live recovery case")
    backend = "victoria"
    signal = "metrics" if case["operation"] == "crash-metrics" else "logs"
    if case["operation"] not in ["crash-metrics", "crash-logs"]:
        raise RuntimeError("No process-failure witness in this case")
    (path / "witness-recovery").mkdir(exist_ok=True)
    assert_source(path / "witness-recovery")
    select_backend(backend, path / "witness-recovery")
    replay_known_loss(path, signal)
    time.sleep(6)
    for item in ["metrics", "logs"]:
        if item == signal:
            continue
        value = history_readback(
            backend,
            item,
            path / "fixture.json",
            path / backend / item,
            path / backend / item / "reconciliation-after-replay.json",
        )
        if value["result"]["missing_acknowledged_ids"]:
            raise RuntimeError("Acknowledged data still missing after witness recovery")
    native_cgroups(backend, path / "witness-recovery", "after-replay")
    disk_snapshot(backend, path / "witness-recovery", "after-replay")
    save(
        path / "witness-recovery/status.json",
        {
            "recovered": True,
            "meaning": "External application witness recovery; original failed result is unchanged",
        },
    )


def replay_known_loss(path, signal):
    if running_actors():
        raise RuntimeError("Cannot replay while a writer is live")
    backend = "victoria"
    directory = path / backend / signal
    report = directory / "reconciliation-before.json"
    started = json.loads((path / "victoria-recovery.json").read_text())["started_at"]
    phase("external-witness-replay:" + backend + ":" + signal)
    result = workload(
        "--mode=replay",
        "--backend=" + backend,
        "--signal=" + signal,
        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
        "--output=/" + str((directory / "replay-first").relative_to(ROOT)),
        "--history=/" + str((directory / "requests.jsonl").relative_to(ROOT)),
        "--missing-report=/" + str(report.relative_to(ROOT)),
        "--ack-before=" + started,
        check=False,
    )
    (directory / "replay-first.log").write_text(result.stdout + "\n" + result.stderr)
    if result.returncode:
        raise RuntimeError(
            "Replay failed or ambiguous; reconcile its journal before retrying"
        )
    time.sleep(6)
    value = history_readback(
        backend,
        signal,
        path / "fixture.json",
        directory,
        directory / "reconciliation-after-replay.json",
    )
    if value["result"]["missing_acknowledged_ids"]:
        raise RuntimeError("Witness replay did not restore every acknowledged record")
