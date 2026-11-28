"""Three fresh-state trials of one paced workload; no capacity-search matrix."""

import json
import subprocess
import time
import traceback
import uuid

from compare import current_fixture
from lab import (
    ROOT,
    PROJECT,
    command,
    workload,
    save,
    phase,
    assert_source,
    select_backend,
    disk_snapshot,
    native_cgroups,
)
from recovery import inspect_actor, history_readback


def measured_backend(backend, path, prior):
    select_backend(backend, path)
    disk_snapshot(backend, path, "before-measurement")
    native_cgroups(backend, path, "before-measurement")
    native_metrics(backend, path, "before")
    actors = []
    errors = []
    phase("measured-baseline:" + path.name + ":" + backend)
    try:
        for signal in ["metrics", "logs"]:
            directory = path / backend / signal
            directory.mkdir(parents=True)
            role = signal + "-writer-reader"
            identifier = (
                command(
                    "run",
                    "--detach",
                    "--name",
                    PROJECT + "-bench-" + path.name + "-" + backend + "-" + signal,
                    "--no-deps",
                    role,
                    "--mode=bench",
                    "--backend=" + backend,
                    "--signal=" + signal,
                    "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
                    "--prior-fixture=/" + str(prior.relative_to(ROOT)),
                    "--output=/" + str(directory.relative_to(ROOT)),
                    "--warmup=60s",
                    "--duration=300s",
                )
                .stdout.strip()
                .splitlines()[-1]
            )
            row = inspect_actor(identifier, role)
            if (
                row["HostConfig"]["NanoCpus"] != 500000000
                or row["HostConfig"]["Memory"] != 256 * 2**20
            ):
                raise RuntimeError("Measured actor budget differs")
            actors.append((identifier, role, directory))
        for identifier, role, directory in actors:
            ended = subprocess.run(
                ["docker", "wait", identifier],
                capture_output=True,
                text=True,
                timeout=420,
            )
            if ended.returncode:
                errors.append("docker wait failed: " + ended.stderr)
    finally:
        for identifier, role, directory in actors:
            row = inspect_actor(identifier, role)
            if row["State"]["Running"]:
                (directory / "stop").touch()
                subprocess.run(
                    ["docker", "wait", identifier],
                    capture_output=True,
                    text=True,
                    timeout=65,
                    check=True,
                )
                row = inspect_actor(identifier, role)
            save(directory / "actor-exit.json", row)
            (directory / "actor.log").write_text(
                subprocess.check_output(
                    ["docker", "logs", identifier], text=True, stderr=subprocess.STDOUT
                )
            )
            if row["State"]["ExitCode"] != 0:
                errors.append("Actor failed; witness retained: " + str(directory))
            else:
                subprocess.run(
                    ["docker", "rm", identifier], check=True, capture_output=True
                )
    phase("measured-readback:" + path.name + ":" + backend)
    if errors:
        raise RuntimeError("; ".join(errors))
    time.sleep(6)
    for signal in ["metrics", "logs"]:
        value = history_readback(
            backend,
            signal,
            path / "fixture.json",
            path / backend / signal,
            path / backend / signal / "reconciliation-before.json",
        )
        if (
            value["result"]["missing_acknowledged_ids"]
            or value["result"]["ambiguous_records"]
        ):
            raise RuntimeError(
                "Healthy measurement had missing or ambiguous writes; no qualified performance claim"
            )
    native_cgroups(backend, path, "after-measurement")
    native_metrics(backend, path, "after")
    disk_snapshot(backend, path, "after-measurement")


def run_matrix(audit):
    from control import bootstrap, initialize, driver_control
    from lifecycle import remove, destroy

    # Preserve the common seed/timestamps across fresh volumes and all six cells.
    seed = json.loads(current_fixture().read_text())
    matrix = ROOT / "results/benchmarks" / uuid.uuid4().hex
    save(
        matrix / "matrix.json",
        {
            "seed": seed,
            "warmup_s": 60,
            "measurement_s": 300,
            "trials": 3,
            "batch": 1000,
            "think_ms": 200,
            "arrival": "two closed-loop writers, each paced after its request; not a saturation/capacity search",
            "durability": "Greptime synchronous WAL versus Victoria default buffered admission; unequal contracts remain explicit",
        },
    )
    passed = False
    try:
        for trial in range(3):
            # This owns only the current lab and never destroys unreconciled input.
            remove(audit)
            destroy()
            phase("benchmark-bootstrap:" + str(trial))
            command("build", "telemetry-workload")
            bootstrap()
            initialize()
            driver_control(False, template=seed)
            prior = current_fixture()
            run = uuid.uuid4().hex
            path = ROOT / "results/cases" / run
            fixture = dict(
                seed,
                run=run,
                payload_seed=seed.get("payload_seed") or seed["run"],
                points=200,
                logs=2000000,
            )
            save(path / "fixture.json", fixture)
            order = (
                ["greptime", "victoria"] if trial % 2 == 0 else ["victoria", "greptime"]
            )
            save(
                path / "case.json",
                {
                    "kind": "measured-baseline",
                    "run": run,
                    "protocol": "otlp",
                    "trial": trial,
                    "order": order,
                    "matrix": str(matrix.relative_to(ROOT)),
                    "prior": str(prior.relative_to(ROOT)),
                },
            )
            complete = False
            try:
                assert_source(path)
                for backend in order:
                    measured_backend(backend, path, prior)
                complete = True
            except BaseException:
                (path / "failure.txt").write_text(traceback.format_exc())
                raise
            finally:
                save(
                    path / "status.json",
                    {
                        "passed": complete,
                        "scope": "Paced healthy baseline with independent body/history readback; not equal-durability capacity ranking",
                    },
                )
        passed = True
    except BaseException:
        (matrix / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(matrix / "status.json", {"passed": passed})


def native_metrics(backend, path, label):
    for signal in (["metrics"] if backend == "greptime" else ["metrics", "logs"]):
        result = workload(
            "--mode=scrape", "--backend=" + backend, "--signal=" + signal, check=False
        )
        (path / (backend + "-" + signal + "-native-" + label + ".prom")).write_text(
            result.stdout
        )
        (path / (backend + "-" + signal + "-native-" + label + ".log")).write_text(
            result.stderr
        )
        if result.returncode:
            raise RuntimeError("Native observability endpoint failed: " + result.stderr)
