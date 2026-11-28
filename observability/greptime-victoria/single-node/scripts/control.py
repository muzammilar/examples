"""Bounded driver/corpus controls before recovery or comparative timing."""

import json
import fcntl
import subprocess
import sys
import time
import traceback
import uuid

from lab import (
    ROOT,
    command,
    workload,
    assert_source,
    select_backend,
    save,
    disk_snapshot,
    phase,
    running_actors,
)


def bootstrap():
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    if info["NCPU"] < 5 or info["MemTotal"] < 7 * 2**30:
        raise RuntimeError("Backend + workload + reserve need at least 5 CPUs / 7 GiB")
    if sys.platform == "darwin":
        model = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True, capture_output=True
        )
        save(
            results / "hardware-augmentation.json",
            {
                "host_cpu_model": (
                    model.stdout.strip() if model.returncode == 0 else "unavailable"
                ),
                "disk_class": "Docker Desktop virtual disk; host device class unverified",
                "captured_at_unix_s": time.time(),
            },
        )
    save(
        results / "docker-environment.json",
        {
            k: info.get(k)
            for k in [
                "NCPU",
                "MemTotal",
                "KernelVersion",
                "OperatingSystem",
                "ServerVersion",
                "Architecture",
            ]
        },
    )
    capacity = workload("--mode=filesystem")
    (results / "filesystem-helper.log").write_text(capacity.stderr)
    measured = json.loads(capacity.stdout)
    save(results / "filesystem-capacity.json", measured)
    if measured["filesystem_available_bytes"] < 8 * 2**30:
        raise RuntimeError("Fewer than 8 GiB free in the local Docker filesystem")
    configuration = json.loads(
        command(
            "--profile",
            "greptime",
            "--profile",
            "victoria",
            "config",
            "--format",
            "json",
        ).stdout
    )
    for name in ["greptime", "vmetrics", "vlogs"]:
        subprocess.run(
            ["docker", "pull", configuration["services"][name]["image"]],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    assert_source(results)
    select_backend("greptime", results)


def initialize():
    path = ROOT / "results/plan.json"
    if path.exists():
        if json.loads(path.read_text()).get("state") == "removed":
            raise RuntimeError(
                "Run make docker-down before creating another generation"
            )
        if json.loads(path.read_text())["corpus"] != "incident-corpus-v1":
            raise RuntimeError("Unexpected existing plan")
        return
    save(
        path,
        {
            "corpus": "incident-corpus-v1",
            "metrics": 1000000,
            "series": 10000,
            "points": 100,
            "logs": 1000000,
            "performance_depth": 3,
            "qualified": False,
            "remaining": [
                "native ingestion",
                "live crash recovery",
                "cold/warm queries",
                "repeated comparisons",
                "verified teardown",
            ],
        },
    )


def verify(backend, signal, path, fixture, protocol="otlp"):
    until = time.monotonic() + 120
    attempt = 0
    while True:
        attempt += 1
        result = workload(
            "--mode=verify",
            "--backend=" + backend,
            "--signal=" + signal,
            "--protocol=" + protocol,
            "--fixture="
            + str(fixture.relative_to(ROOT)).replace("results/", "/results/", 1),
            check=False,
        )
        (path / f"verify-{attempt}.log").write_text(
            result.stdout + "\n" + result.stderr
        )
        if result.returncode == 0:
            return
        messages = [
            json.loads(line).get("error", "")
            for line in result.stderr.splitlines()
            if line.startswith('{"error"') or line.startswith('{"event"')
        ]
        pending = any(
            m.startswith(("missing metric samples:", "missing log id="))
            for m in messages
        )
        if not pending or time.monotonic() >= until:
            raise RuntimeError(
                "Exact readback failed; preserve this fixture: " + result.stderr
            )
        time.sleep(1)


def audit_retained():
    if running_actors():
        raise RuntimeError("Stop live actors before a frozen-history audit")
    audit = ROOT / "results/audits" / uuid.uuid4().hex
    audit.mkdir(parents=True)
    verified, passed = [], False
    try:
        assert_source(audit)
        for case in sorted((ROOT / "results/cases").glob("*/case.json")):
            from lifecycle import is_retired

            if is_retired(case):
                continue
            if json.loads(case.read_text())["kind"] in [
                "live-recovery",
                "measured-baseline",
            ]:
                from recovery import verify_live_case

                verify_live_case(case, audit)
                continue
            fixture = case.parent / "fixture.json"
            protocol = json.loads(case.read_text()).get("protocol", "otlp")
            for backend in ["greptime", "victoria"]:
                journals = [
                    (signal, case.parent / backend / signal / "requests.jsonl")
                    for signal in ["metrics", "logs"]
                ]
                journals = [
                    (signal, path) for signal, path in journals if path.exists()
                ]
                if not journals:
                    continue
                select_backend(backend, audit)
                for signal, journal in journals:
                    rows = [
                        json.loads(line) for line in journal.read_text().splitlines()
                    ]
                    responses = [r for r in rows if r["event"] == "response"]
                    if len(responses) != sum(
                        r["event"] == "intent" for r in rows
                    ) or any(r["outcome"] != "accepted" for r in responses):
                        raise RuntimeError(
                            "Uncertain prior ingestion requires explicit reconciliation: "
                            + str(journal)
                        )
                    target = audit / case.parent.name / backend / signal
                    target.mkdir(parents=True)
                    phase("retained:" + backend + ":" + signal)
                    verify(backend, signal, target, fixture, protocol)
                    verified.append(str(journal.relative_to(ROOT)))
        passed = True
    except BaseException:
        (audit / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(audit / "status.json", {"passed": passed, "verified_journals": verified})


def driver_control(small, protocol="otlp", template=None):
    if not (ROOT / "results/plan.json").exists():
        raise RuntimeError("Run make up first")
    run = uuid.uuid4().hex
    path = ROOT / "results/cases" / run
    path.mkdir(parents=True)
    fixture = path / "fixture.json"
    data = {
        "run": run,
        "start_ms": (int(time.time()) - 7200) * 1000,
        "series": 100 if small else 10000,
        "points": 100,
        "logs": 10000 if small else 1000000,
    }
    if template is not None:
        data = dict(
            template,
            run=run,
            payload_seed=template.get("payload_seed") or template["run"],
        )
    if protocol == "native" and not small:
        candidates = [
            p
            for p in (ROOT / "results/cases").glob("*/case.json")
            if json.loads(p.read_text())["kind"] == "healthy-common-otlp"
            and json.loads((p.parent / "status.json").read_text())["passed"]
        ]
        if not candidates:
            raise RuntimeError(
                "Qualify the million-record common corpus before native comparison"
            )
        prior = max(candidates, key=lambda p: p.stat().st_mtime_ns)
        data = json.loads((prior.parent / "fixture.json").read_text())
        data["payload_seed"] = data.get("payload_seed") or data["run"]
        data["run"] = run
    save(fixture, data)
    save(
        path / "case.json",
        {
            "kind": (
                "driver-control"
                if small
                else ("healthy-common-otlp" if protocol == "otlp" else "healthy-native")
            ),
            "run": run,
            "protocol": protocol,
        },
    )
    passed = False
    try:
        assert_source(path)
        for backend in ["greptime", "victoria"]:
            select_backend(backend, path)
            disk_snapshot(backend, path, "before-ingest")
            for signal in ["metrics", "logs"]:
                output = path / backend / signal
                output.parent.mkdir(exist_ok=True)
                phase(backend + ":push:" + signal)
                result = workload(
                    "--backend=" + backend,
                    "--signal=" + signal,
                    "--batch=1000",
                    "--protocol=" + protocol,
                    "--fixture=/results/cases/" + run + "/fixture.json",
                    "--output=/results/cases/" + run + "/" + backend + "/" + signal,
                    check=False,
                )
                output.mkdir(exist_ok=True)
                (output / "push.log").write_text(result.stdout + "\n" + result.stderr)
                if result.returncode:
                    raise RuntimeError(
                        "Ingestion failed; reconcile its retained journal: "
                        + result.stderr
                    )
                phase(backend + ":verify:" + signal)
                verify(backend, signal, output, fixture, protocol)
            disk_snapshot(backend, path, "after-readback")
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
                "scope": "Healthy "
                + protocol
                + " input/readback only; no recovery/performance qualification",
            },
        )


def dispatch():
    mode = sys.argv[1]
    if mode == "docker-up":
        bootstrap()
    elif mode == "up":
        initialize()
    elif mode in ["live-control", "crash-metrics", "crash-logs"]:
        from recovery import run_case

        run_case(mode, audit_retained)
    elif mode == "benchmark":
        from benchmark import run_matrix

        run_matrix(audit_retained)
    elif mode in ["batch-screen", "query-screen"]:
        from compare import batch_screen, query_screen

        {"batch-screen": batch_screen, "query-screen": query_screen}[mode](
            audit_retained
        )
    elif mode == "recover-witness":
        from recovery import recover_witness

        recover_witness()
    elif mode == "down":
        from lifecycle import remove

        remove(audit_retained)
    elif mode == "docker-down":
        from lifecycle import destroy

        destroy()
    elif mode == "native-correctness":
        audit_retained()
        driver_control(False, "native")
    elif mode == "native-control":
        audit_retained()
        driver_control(True, "native")
    elif mode == "verify-retained":
        audit_retained()
    elif mode in ["driver-control", "correctness"]:
        audit_retained()
        driver_control(mode == "driver-control")
    else:
        raise ValueError(mode)


def main():
    (ROOT / "results").mkdir(exist_ok=True)
    with (ROOT / "results/controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "Another lifecycle/fault controller owns this example"
            ) from error
        phase(sys.argv[1])
        dispatch()


if __name__ == "__main__":
    main()
