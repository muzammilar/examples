"""Live three-signal diagnostics; exact fidelity and value recovery stay separate."""

import json
from datetime import datetime
import threading
import subprocess
import time
import traceback
import uuid

from lifecycle import is_retired

from lab import (
    ROOT,
    PROJECT,
    BACKENDS,
    command,
    owned,
    save,
    phase,
    assert_source,
    select_backend,
    fence,
    verify,
    running_actors,
    disk_snapshot,
    metrics_snapshot,
)


def events(path):
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raw = raw.rsplit(b"\n", 1)[0] if b"\n" in raw else b""
    return [json.loads(line) for line in raw.splitlines() if line]


def actor(identifier, role):
    row = json.loads(
        subprocess.check_output(["docker", "inspect", identifier], text=True)
    )[0]
    labels = row["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != role
    ):
        raise RuntimeError("Foreign actor")
    return row


def stop_actor(identifier, role, directory):
    row = actor(identifier, role)
    if row["State"]["Running"]:
        (directory / "stop").touch()
        subprocess.run(
            ["docker", "wait", identifier],
            check=True,
            capture_output=True,
            text=True,
            timeout=65,
        )
        row = actor(identifier, role)
    (directory / (role + ".log")).write_text(
        subprocess.check_output(
            ["docker", "logs", identifier], text=True, stderr=subprocess.STDOUT
        )
    )
    save(directory / (role + "-exit.json"), row)
    if row["State"]["ExitCode"] != 0:
        raise RuntimeError("Actor failed; retain witness: " + row["Name"])
    subprocess.run(["docker", "rm", identifier], check=True, capture_output=True)


def readback(backend, fixture, directory, destination):
    destination.mkdir(parents=True, exist_ok=True)
    until = time.monotonic() + 120
    attempt = 0
    while True:
        attempt += 1
        value, missing = verify(
            backend,
            fixture,
            directory,
            destination / ("readback-" + str(attempt) + ".json"),
            projection=backend == "victoria",
        )
        if not missing:
            return value
        if time.monotonic() >= until:
            raise RuntimeError(
                "ACK loss remains after convergence; preserve before any recovery"
            )
        time.sleep(2)


def audit_retained(selected=None):
    if running_actors():
        raise RuntimeError("A live actor prevents a frozen-history audit")
    path = ROOT / "results/audits" / uuid.uuid4().hex
    assert_source(path)
    passed = False
    try:
        for case in sorted((ROOT / "results/cases").glob("*/case.json")):
            if is_retired(case):
                continue
            for backend in ([selected] if selected else BACKENDS):
                directory = case.parent / backend
                if not (directory / "fenced.json").exists():
                    if any(directory.glob("*/requests.jsonl")):
                        raise RuntimeError(
                            "Unfenced publication must be reconciled before continuing: "
                            + str(directory)
                        )
                    continue
                if backend == "victoria":
                    # This does NOT qualify the failed strict type contract.
                    # A separate preserved diagnostic proved the exact string
                    # projection; the original failure is never overwritten.
                    known = list(
                        case.parent.glob("projection-diagnostics/*/status.json")
                    )
                    if json.loads(case.read_text())[
                        "kind"
                    ] == "driver-control" and not any(
                        json.loads(p.read_text()).get("all_ack_values_present")
                        for p in known
                    ):
                        raise RuntimeError(
                            "Original typed failure needs its explicit diagnostic first"
                        )
                select_backend(backend, path)
                readback(
                    backend,
                    case.parent / "fixture.json",
                    directory,
                    path / case.parent.name / backend,
                )
        passed = True
    finally:
        save(
            path / "status.json",
            {
                "all_ack_values_present": passed,
                "scope": "Value/history reconciliation only; Victoria trace exact-type failure remains unqualified",
            },
        )


def live_ready(directory, after=None):
    reads = events(directory / "reader/reads.jsonl")
    for signal in ["metrics", "logs", "traces"]:
        for kind in ["old", "recent"]:
            candidates = [
                r
                for r in reads
                if r["signal"] == signal
                and r["kind"] == kind
                and r["result"] in ["visible", "fidelity-mismatch"]
            ]
            if after is not None and kind == "recent":
                candidates = [
                    r
                    for r in candidates
                    if datetime.fromisoformat(
                        r["ack_completed_at"].replace("Z", "+00:00")
                    )
                    > datetime.fromisoformat(after.replace("Z", "+00:00"))
                ]
            if not candidates:
                return False
    return True


def wait_ready(directory, actors, after=None):
    end = time.monotonic() + 120
    while time.monotonic() < end:
        for identifier, role in actors:
            if not actor(identifier, role)["State"]["Running"]:
                raise RuntimeError("Actor stopped before readiness/recovery")
        if live_ready(directory, after):
            return
        time.sleep(0.5)
    raise TimeoutError("Live old/recent visibility did not recover")


def run(kind, resume=None, selected=None):
    if kind not in ["healthy", "collector-crash"]:
        raise ValueError(kind)
    if selected is not None and selected not in BACKENDS:
        raise ValueError("Unknown selected backend")
    audit_retained(selected)
    if resume is None:
        controls = [
            p
            for p in (ROOT / "results/cases").glob("*/case.json")
            if json.loads(p.read_text())["kind"] == "driver-control"
        ]
        if not controls:
            raise RuntimeError("Run the small control first")
        prior = (
            max(controls, key=lambda p: p.stat().st_mtime_ns).parent / "fixture.json"
        )
        fixture = json.loads(prior.read_text())
        run = uuid.uuid4().hex
        fixture.update(run=run, requests=10000)
        path = ROOT / "results/cases" / run
        save(path / "fixture.json", fixture)
        save(
            path / "case.json",
            {
                "kind": kind,
                "backends": [selected] if selected else list(BACKENDS),
                "run": run,
                "prior": str(prior.relative_to(ROOT)),
                "scope": "Million-record live value/recovery diagnostic; Victoria exact numeric attribute fidelity remains failed",
            },
        )
        result_path = path
    else:
        if len(resume) != 32 or any(c not in "0123456789abcdef" for c in resume):
            raise ValueError("Invalid preserved case ID")
        path = ROOT / "results/cases" / resume
        metadata = json.loads((path / "case.json").read_text())
        if metadata["kind"] != kind:
            raise RuntimeError("Case kind differs")
        prior = ROOT / metadata["prior"]
        fixture = json.loads((path / "fixture.json").read_text())
        run = resume
        result_path = path / "resumptions" / uuid.uuid4().hex
        result_path.mkdir(parents=True)
    backends = json.loads((path / "case.json").read_text()).get(
        "backends", list(BACKENDS)
    )
    values_passed = False
    exact = True
    try:
        assert_source(result_path)
        for backend in backends:
            select_backend(backend, result_path)
            directory = path / backend
            if directory.exists():
                if not (directory / "fenced.json").exists():
                    raise RuntimeError(
                        "Prior unfenced publication needs explicit reconciliation"
                    )
                value = readback(
                    backend,
                    path / "fixture.json",
                    directory,
                    result_path / backend / "retained",
                )
                exact = exact and all(
                    s["exact_attribute_type_fidelity"]
                    for s in value["signals"].values()
                )
                continue
            disk_snapshot(backend, path, "before-ingest")
            metrics_snapshot(backend, path, "before-ingest")
            sampler_stop = threading.Event()
            sampler_errors = []

            def sample_disk():
                iteration = 0
                while not sampler_stop.wait(30):
                    iteration += 1
                    try:
                        disk_snapshot(backend, path, "sample-" + str(iteration))
                    except Exception as error:
                        sampler_errors.append(str(error))
                        return

            sampler = threading.Thread(target=sample_disk)
            sampler.start()
            actors = []
            collector = None
            try:
                phase("live-publish:" + backend)
                for role, mode in [
                    ("incident-publisher", "publish"),
                    ("incident-reader", "watch"),
                ]:
                    if role == "incident-reader":
                        until = time.monotonic() + 20
                        while not (directory / "configuration.json").exists():
                            if time.monotonic() > until:
                                raise TimeoutError(
                                    "Publisher did not create its source configuration"
                                )
                            time.sleep(0.1)
                    arguments = [
                        "run",
                        "--detach",
                        "--no-deps",
                        "--name",
                        PROJECT + "-" + run + "-" + backend + "-" + mode,
                        role,
                        "--mode=" + mode,
                        "--backend=" + backend,
                        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
                        "--output=/" + str(directory.relative_to(ROOT)),
                    ]
                    if mode == "watch":
                        arguments.append(
                            "--prior-fixture=/" + str(prior.relative_to(ROOT))
                        )
                    identifier = command(*arguments).stdout.strip().splitlines()[-1]
                    actors.append((identifier, role))
                    row = actor(identifier, role)
                    expected = (
                        (750000000, 384 * 2**20)
                        if mode == "publish"
                        else (250000000, 128 * 2**20)
                    )
                    if (
                        row["HostConfig"]["NanoCpus"],
                        row["HostConfig"]["Memory"],
                    ) != expected:
                        raise RuntimeError("Live actor budget differs")
                wait_ready(directory, actors)
                if kind == "collector-crash":
                    collector = owned(backend + "-collector")
                    began = time.monotonic()
                    phase("fault:" + backend + ":collector")
                    subprocess.run(
                        ["docker", "kill", "--signal", "KILL", collector["Id"]],
                        check=True,
                        capture_output=True,
                    )
                    killed = owned(backend + "-collector")
                    if (
                        killed["State"]["Running"]
                        or killed["State"]["ExitCode"] != 137
                        or killed["State"]["OOMKilled"]
                    ):
                        raise RuntimeError("Collector SIGKILL was not demonstrated")
                    save(path / (backend + "-collector-killed.json"), killed)
                    while time.monotonic() - began < 10:
                        for identifier, role in actors:
                            if not actor(identifier, role)["State"]["Running"]:
                                raise RuntimeError("Actor stopped during held fault")
                        time.sleep(0.5)
                    phase("recovery:" + backend + ":collector")
                    subprocess.run(
                        ["docker", "start", collector["Id"]],
                        check=True,
                        capture_output=True,
                    )
                    started = owned(backend + "-collector")["State"]["StartedAt"]
                    wait_ready(directory, actors, started)
                    save(
                        path / (backend + "-collector-recovery.json"),
                        {
                            "kill_to_new_ack_visible_s": time.monotonic() - began,
                            "started_at": started,
                            "same_container": owned(backend + "-collector")["Id"]
                            == collector["Id"],
                        },
                    )
                subprocess.run(
                    ["docker", "wait", actors[0][0]],
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=660,
                )
            finally:
                if (
                    collector is not None
                    and not owned(backend + "-collector")["State"]["Running"]
                ):
                    subprocess.run(
                        ["docker", "start", collector["Id"]],
                        check=True,
                        capture_output=True,
                    )
                sampler_stop.set()
                sampler.join(timeout=180)
                if sampler.is_alive():
                    sampler_errors.append("Disk sampler failed to stop")
                failures = list(sampler_errors)
                for identifier, role in actors:
                    try:
                        stop_actor(identifier, role, directory)
                    except Exception as error:
                        failures.append(str(error))
                if failures:
                    raise RuntimeError("; ".join(failures))
            publication = events(directory / "incident-publisher.log")
            publication = [r for r in publication if "planned_items" in r]
            if len(publication) != 3 or any(
                not r["completed"] or r["emitted_items"] != r["planned_items"]
                for r in publication
            ):
                raise RuntimeError(
                    "Publisher did not attempt its complete declared corpus"
                )
            if kind == "healthy" and any(
                r["unacknowledged_requests"] for r in publication
            ):
                raise RuntimeError("Healthy corpus had unacknowledged source requests")
            phase("fenced-readback:" + backend)
            fence(directory)
            time.sleep(6)
            value = readback(
                backend, path / "fixture.json", directory, directory / "final"
            )
            exact = exact and all(
                s["exact_attribute_type_fidelity"] for s in value["signals"].values()
            )
            disk_snapshot(backend, path, "after-actors")
            metrics_snapshot(backend, path, "after-actors")
        values_passed = True
    except BaseException:
        (result_path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(
            result_path / "status.json",
            {
                "values_and_recovery_passed": values_passed,
                "exact_type_fidelity": exact if values_passed else False,
                "passed": values_passed and exact,
                "scope": "Exact typed qualification remains failed if any numeric projection is required; no capacity winner",
            },
        )
    if not exact:
        raise RuntimeError(
            "All ACK values reconciled; Victoria typed trace qualification remains failed. See separate projection count."
        )
