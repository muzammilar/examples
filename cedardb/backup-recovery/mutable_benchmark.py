"""Matched fresh-process analytics cells on data changed by transactional updates/deletes."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time
import uuid

import control
from htap import bounded_setting, host_rss_bytes, query_summary
from mutable_lab import MutableCase, sample_disk
from resources import Recorder, summarize_records


def verify_fixture(lab, requested_rows):
    if lab["phase"] != "ready" or lab["rows"] != requested_rows:
        raise RuntimeError(
            "ROWS differs from the ready retained fixture; recreate it explicitly"
        )


def measured_samples(samples, start, end):
    return [
        sample
        for sample in samples
        if start <= sample["started_ns"]
        and sample["ended_ns"] <= end
        and start
        <= sample.get("disk", {}).get("native_io_captured_ns", sample["ended_ns"])
        <= end
    ]


def live_summary(events, start, end, seconds):
    if seconds <= 0 or abs((end - start) / 1e9 - seconds) > 0.01:
        raise RuntimeError(
            "live-window UTC and monotonic clocks differ by more than 10ms"
        )
    summary = control.phase_summary(events, start, end)
    for metrics in summary.values():
        metrics["rate"] = metrics["count"] / seconds
    return summary


def verify_cohort(config):
    lab = json.loads((control.RESULTS / "lab.json").read_text())
    verify_fixture(lab, config["rows"])
    if lab["generation"] != config["base_generation"]:
        raise RuntimeError("base fixture generation changed during cohort")
    for name, digest in config["source_sha256"].items():
        if hashlib.sha256((control.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("runtime source changed during cohort: " + name)
    current_image = json.loads(
        control.run(["docker", "image", "inspect", "cedardb-recovery-orders:dev"])
    )[0]["Id"]
    if current_image != config["workload_image"]:
        raise RuntimeError("workload image changed during cohort")
    for backend, image in config["database_images"].items():
        if control.container(backend)["Image"] != image:
            raise RuntimeError("database image changed during cohort")


def stop_database(backend, root, label):
    info = control.container(backend)
    if info["State"]["Running"]:
        control.compose("stop", backend)
        info = control.container(backend)
        control.save(root / (label + "-stopped.json"), info)
        if info["State"]["Running"] or info["State"]["ExitCode"] != 0:
            raise RuntimeError("fresh-process cell requires a clean database stop")


def close_analytics(case, name, identity, process):
    try:
        if identity is None:
            found = control.run(
                ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"]
            ).split()
            identity = found[0] if found else None
        if identity is None:
            return
        info = control.inspect(identity)
        if info["Config"]["Labels"].get("org.examples.mutable-case") != case.identity:
            raise RuntimeError("cannot clean another case's analytics container")
        if info["State"]["Running"]:
            control.run(["docker", "stop", "--timeout", "10", identity])
        info = control.inspect(identity)
        control.save(case.root / "analytics-final.json", info)
        if info["State"]["Running"] or info["State"]["ExitCode"]:
            raise RuntimeError(
                "analytical shutdown failed; stopped container preserved"
            )
        control.run(["docker", "rm", identity])
    finally:
        if process is not None and process.poll() is None:
            process.wait(timeout=15)


def cell(backend, concurrent, trial, warmup, duration, pairs):
    case = MutableCase(backend, pairs)
    case.config.update(
        concurrent=concurrent, trial=trial, warmup_s=warmup, duration_s=duration
    )
    control.save(case.root / "config.json", case.config)
    name = "cedar-mutable-analytics-" + case.identity[:12]
    process, identity, result = None, None, None
    passed = False
    started = time.monotonic()
    cpu_before = resource.getrusage(resource.RUSAGE_SELF)
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        other = next(b for b in control.BACKENDS if b != backend)
        stop_database(other, case.root, "inactive-backend")
        stop_database(backend, case.root, "fresh-backend")
        control.compose("up", "--detach", "--wait", "--no-deps", backend)
        current = control.container(backend)
        config = current["HostConfig"]
        if config["NanoCpus"] != 4 * 10**9 or any(
            config[k] != 6 * 1024**3 for k in ("Memory", "MemorySwap")
        ):
            raise RuntimeError("mutable database budget differs")
        context = json.loads(control.run(["docker", "context", "inspect"]))[0]
        endpoint = (
            os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
        )
        targets = [(backend, current["Id"])]
        before = control.native_snapshot(backend)
        with Recorder(
            case.root / "analytics-resources.jsonl",
            endpoint,
            targets,
            sample_disk,
        ) as recorder:
            case.seed()
            initialized = case.snapshot()
            if concurrent:
                case.start()
                recorder.targets.extend(case.actors.items())
                case.progress()
            with (
                (case.root / "analytics.log").open("w") as output,
                (case.root / "analytics.stderr").open("w") as errors,
                (case.root / "analytics.log").open() as phase_events,
            ):
                process = subprocess.Popen(
                    [
                        "docker",
                        "compose",
                        "run",
                        "--no-deps",
                        "--name",
                        name,
                        "--label",
                        "org.examples.mutable-case=" + case.identity,
                        "-T",
                        "orders-workload",
                        "mutable-analytics",
                    ],
                    cwd=control.ROOT,
                    env=control.ENV
                    | case.env
                    | {
                        "ANALYTICS_WARMUP_S": str(warmup),
                        "ANALYTICS_DURATION_S": str(duration),
                    },
                    stdout=output,
                    stderr=errors,
                )
                deadline = time.monotonic() + warmup + duration + 90
                while process.poll() is None:
                    if concurrent:
                        case.assert_running()
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "mutable analytical client exceeded bounded deadline"
                        )
                    if identity is None:
                        found = control.run(
                            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"]
                        ).split()
                        if found:
                            info = control.inspect(found[0])
                            if (
                                info["Config"]["Labels"].get(
                                    "org.examples.mutable-case"
                                )
                                != case.identity
                            ):
                                raise RuntimeError("analytics case ownership differs")
                            identity = info["Id"]
                            recorder.targets.append(("analytics", identity))
                    while True:
                        position = phase_events.tell()
                        line = phase_events.readline()
                        if not line.endswith("\n"):
                            phase_events.seek(position)
                            break
                        event = json.loads(line)
                        if event.get("kind") == "phase-start":
                            recorder.phase = event["phase"]
                    time.sleep(1)
                if process.returncode:
                    raise RuntimeError(
                        "mutable analytical client failed; inspect its stderr"
                    )
            info = control.inspect(identity or name)
            identity = info["Id"]
            control.save(case.root / "analytics-stopped.json", info)
            if info["State"]["Running"] or info["State"]["ExitCode"]:
                raise RuntimeError("mutable analytical container did not exit cleanly")
            if info["HostConfig"]["NanoCpus"] != 10**9 or any(
                info["HostConfig"][k] != 512 * 1024**2 for k in ("Memory", "MemorySwap")
            ):
                raise RuntimeError("mutable analytical client budget differs")
            case.close()
            recorder.phase = "full-verification"
            proof = case.verify(concurrent)
            after = case.snapshot()
        records = [
            json.loads(line)
            for line in (case.root / "analytics.log").read_text().splitlines()
        ]
        queries = query_summary(records)
        starts = {
            r["phase"]: r["started_ns"]
            for r in records
            if r.get("kind") == "phase-start"
        }
        ends = {r["phase"]: r for r in records if r.get("kind") == "phase-end"}
        if any(not r["passed"] for r in records if r.get("kind") == "analytic-query"):
            raise RuntimeError("mutable query failed, including warmup")
        if (
            ends["warmup"]["elapsed_ns"] < warmup * 10**9
            or ends["measurement"]["elapsed_ns"] < duration * 10**9
        ):
            raise RuntimeError("native monotonic window is shorter than requested")
        start, end = starts["measurement"], ends["measurement"]["ended_ns"]
        live = {}
        for role in case.actors:
            events = [e for e in case.events(role) if e.get("ended_ns", 0) <= end]
            live[role] = live_summary(events, start, end, queries["measured_seconds"])
            total = sum(value["count"] for value in live[role].values())
            if (
                live[role]["ok"]["count"] < 5
                or live[role]["ok"]["count"] / max(total, 1) < 0.95
            ):
                raise RuntimeError(
                    "live mutable client did not sustain the success contract"
                )
        samples = [
            json.loads(line)
            for line in (case.root / "analytics-resources.jsonl")
            .read_text()
            .splitlines()
        ]
        measured = summarize_records(measured_samples(samples, start, end))
        expected_roles = {backend, "analytics"} | (
            {"writer", "reader"} if concurrent else set()
        )
        if set(measured) != expected_roles or any(
            v["observed_cpu_core_seconds"] is None
            or v["sampled_charged_memory_bytes_min_mean_max"] is None
            for v in measured.values()
        ):
            raise RuntimeError("required measured CPU/memory evidence is missing")
        native_io = measured[backend]["io_and_network"]
        if not all(
            any(
                k.startswith("native_block_")
                and k.endswith(suffix)
                and v["observed_seconds"] > 0
                for k, v in native_io.items()
            )
            for suffix in (
                "read_bytes",
                "write_bytes",
                "read_operations",
                "write_operations",
            )
        ):
            raise RuntimeError("measurement lacks native disk-byte/IOPS intervals")
        result = {
            "passed": False,
            "scenario_passed": True,
            "backend": backend,
            "concurrent": concurrent,
            "trial": trial,
            "identity": case.identity,
            "query": queries,
            "live_workload": live,
            "verification": proof,
            "native_before": before,
            "native_initialized": initialized,
            "native_after": after,
            "process_resources": [
                r for r in records if r.get("kind") == "process-resources"
            ],
            "measurement_resources": measured,
            "all_resources": summarize_records(samples),
            "workload_image": info["Image"],
            "warmup_s": warmup,
            "duration_s": duration,
            "live_rate_clock": "Counts selected by UTC phase markers; rates use the analytical application's direct monotonic measurement interval, within a 10ms UTC guard",
            "scope": "Fixed aggregate invariants over actually updated/replaced rows; fresh database process/schema and warm queries, retained immutable base volumes, no OS-cold or saturation claim",
        }
        control.save(case.root / "benchmark-result.json", result)
        case.drop_verified()
        stop_database(backend, case.root, "completed-backend")
        passed = True
    except Exception as error:
        control.save(
            case.root / "failure.json",
            {"passed": False, "error": repr(error), "stage": "mutable-benchmark"},
        )
        raise
    finally:
        cleanup_errors = []
        for cleanup in (
            case.close,
            lambda: close_analytics(case, name, identity, process),
        ):
            try:
                cleanup()
            except Exception as error:
                cleanup_errors.append(repr(error))
        after = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        control.save(
            case.root / "controller.json",
            {
                "wall_s": time.monotonic() - started,
                "cpu_core_s": after.ru_utime
                + after.ru_stime
                - cpu_before.ru_utime
                - cpu_before.ru_stime,
                "children_cpu_core_s": children.ru_utime
                + children.ru_stime
                - children_before.ru_utime
                - children_before.ru_stime,
                "peak_rss_bytes": host_rss_bytes(after.ru_maxrss),
                "children_peak_rss_bytes": host_rss_bytes(children.ru_maxrss),
                "scope": "Host controller/CLI costs, excluding engines and Go containers; peaks are process lifetime, not independent per-cell peaks",
                "scenario_passed": passed,
                "cleanup_errors": cleanup_errors,
            },
        )
        if cleanup_errors:
            raise RuntimeError(
                "mutable benchmark cleanup failed: " + repr(cleanup_errors)
            )
    result["passed"] = True
    control.save(case.root / "benchmark-result.json", result)
    print(
        "Mutable benchmark cell passed:",
        backend,
        concurrent,
        trial,
        case.root,
        flush=True,
    )
    return result


def main():
    trials = bounded_setting("TRIALS", 3, 1, 10)
    warmup = bounded_setting("ANALYTICS_WARMUP_S", 60, 0, 3600)
    duration = bounded_setting("ANALYTICS_DURATION_S", 300, 1, 3600)
    lab = json.loads((control.RESULTS / "lab.json").read_text())
    verify_fixture(lab, int(control.ENV.get("ROWS", "1000000")))
    pairs = (
        bounded_setting(
            "MUTABLE_PAIRS",
            min(10000, lab["rows"] // 200 * 100 - 1),
            1,
            lab["rows"] // 200 * 100 - 1,
        )
        if os.environ.get("MUTABLE_PAIRS")
        else min(10000, lab["rows"] // 200 * 100 - 1)
    )
    with (control.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        control.owned_inventory()
        input_proofs = {
            backend: control.reconcile(backend) for backend in control.BACKENDS
        }
        if (
            len(
                {
                    (p["seed_rows"], p["csv_sha256"], p["csv_bytes"])
                    for p in input_proofs.values()
                }
            )
            != 1
        ):
            raise RuntimeError("immutable base fixtures differ between backends")
        batch = control.RESULTS / "mutable-benchmark" / uuid.uuid4().hex
        config = {
            "created_ns": time.time_ns(),
            "trials": trials,
            "warmup_s": warmup,
            "duration_s": duration,
            "rows": lab["rows"],
            "input_fixture": input_proofs,
            "host_hardware": control.run(
                [
                    "/usr/sbin/sysctl",
                    "machdep.cpu.brand_string",
                    "hw.memsize",
                    "hw.physicalcpu",
                    "hw.logicalcpu",
                ]
            )
            if os.uname().sysname == "Darwin"
            else {
                "uname": list(os.uname()),
                "cpuinfo": Path("/proc/cpuinfo").read_text(),
            },
            "pairs": pairs,
            "rate": int(control.ENV.get("RATE", "50")),
            "diagnostic_only": trials < 3 or warmup < 60 or duration < 300,
            "base_generation": lab["generation"],
            "workload_image": json.loads(
                control.run(
                    ["docker", "image", "inspect", "cedardb-recovery-orders:dev"]
                )
            )[0]["Id"],
            "database_images": {
                backend: control.container(backend)["Image"]
                for backend in control.BACKENDS
            },
            "source_sha256": {
                str(p.relative_to(control.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    control.ROOT / "mutable_lab.py",
                    control.ROOT / "mutable_oracle.py",
                    control.ROOT / "control.py",
                    control.ROOT / "resources.py",
                    control.ROOT / "htap.py",
                    control.ROOT / "docker-compose.yml",
                    *sorted((control.ROOT / "orders-workload").glob("*")),
                ]
                if p.is_file()
            },
        }
        config["backend_contract"] = json.loads(
            (control.ROOT / "mutable-contract.json").read_text()
        )
        config["source_sha256"]["mutable-contract.json"] = hashlib.sha256(
            (control.ROOT / "mutable-contract.json").read_bytes()
        ).hexdigest()
        control.save(batch / "config.json", config)
        headers = sorted(
            control.RESULTS.glob("environment-*.json"),
            key=lambda p: p.stat().st_mtime_ns,
        )
        control.save(batch / "environment.json", json.loads(headers[-1].read_text()))
        results = []
        for trial in range(trials):
            backends = (
                control.BACKENDS
                if trial % 2 == 0
                else tuple(reversed(control.BACKENDS))
            )
            for concurrent in (False, True) if trial % 2 == 0 else (True, False):
                for backend in backends:
                    verify_cohort(config)
                    print("Mutable cell:", trial + 1, backend, concurrent, flush=True)
                    results.append(
                        cell(backend, concurrent, trial + 1, warmup, duration, pairs)
                    )
                    verify_cohort(config)
                    if results[-1]["workload_image"] != config["workload_image"]:
                        raise RuntimeError(
                            "executed analytical image differs from cohort"
                        )
                    control.save(
                        batch / "results.json",
                        {
                            "config": config,
                            "cells": results,
                            "complete": len(results) == trials * 4,
                            "finalized": False,
                        },
                    )
        control.compose("up", "--detach", "--wait", *control.BACKENDS)
        control.save(
            batch / "results.json",
            {"config": config, "cells": results, "complete": True, "finalized": True},
        )
        print("Mutable cohort completed:", batch, flush=True)


if __name__ == "__main__":
    main()
