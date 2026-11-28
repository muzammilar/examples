"""Bring up and inspect the pinned three-signal profiles; evidence is immutable."""

import fcntl
import json
import subprocess
import sys
import time
import traceback
import uuid

from lab import (
    ROOT,
    BACKENDS,
    command,
    tool,
    save,
    phase,
    assert_source,
    select_backend,
    fence,
    verify,
)


def initialize():
    path = ROOT / "results/plan.json"
    if path.exists():
        if json.loads(path.read_text())["corpus"] != "checkout-incident-v1":
            raise RuntimeError("Unexpected existing corpus plan")
        return
    save(
        path,
        {
            "corpus": "checkout-incident-v1",
            "requests": 10000,
            "logs": 1000000,
            "metrics": 1000000,
            "spans": 30000,
            "performance_depth": 3,
            "state": "active",
        },
    )


def bootstrap():
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    if info["NCPU"] < 8 or info["MemTotal"] < 11 * 2**30:
        raise RuntimeError("Backend, workload and reserve need 8 CPU / 11 GiB")
    save(
        ROOT / "results/environment.json",
        {
            key: info.get(key)
            for key in [
                "NCPU",
                "MemTotal",
                "KernelVersion",
                "OperatingSystem",
                "ServerVersion",
                "Architecture",
            ]
        },
    )
    capacity = tool("--mode=filesystem")
    measured = json.loads(capacity.stdout)
    save(ROOT / "results/filesystem.json", measured)
    if measured["filesystem_available_bytes"] < 8 * 2**30:
        raise RuntimeError("Fewer than 8 GiB free in Docker's filesystem")
    configuration = json.loads(
        command(
            "--profile",
            "greptime",
            "--profile",
            "victoria",
            "--profile",
            "grafana",
            "--profile",
            "tools",
            "config",
            "--format",
            "json",
        ).stdout
    )
    save(ROOT / "results/compose-configuration.json", configuration)
    for role in [role for roles in BACKENDS.values() for role in roles]:
        image = configuration["services"][role]["image"]
        with (ROOT / "results" / ("pull-" + role + ".log")).open("w") as log:
            subprocess.run(
                ["docker", "pull", image],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    assert_source(ROOT / "results")
    initialize()
    select_backend("greptime", ROOT / "results")


def driver_control(resume=None, selected=None):
    if resume is None:
        if any((ROOT / "results/cases").glob("*/case.json")):
            raise RuntimeError(
                "An earlier publication exists: resume/reconcile it first"
            )
        run = uuid.uuid4().hex
        path = ROOT / "results/cases" / run
        fixture = {
            "run": run,
            "payload_seed": run,
            "start_ms": (int(time.time()) - 7200) * 1000,
            "requests": 100,
            "logs_per_request": 100,
            "metric_points": 100,
        }
        save(path / "fixture.json", fixture)
        save(
            path / "case.json",
            {
                "kind": "driver-control",
                "run": run,
                "scope": "Small native mapping/correctness control; no performance claim",
            },
        )
    else:
        if len(resume) != 32 or any(c not in "0123456789abcdef" for c in resume):
            raise ValueError("CASE must be a preserved 32-hex case ID")
        run = resume
        path = ROOT / "results/cases" / run
        if json.loads((path / "case.json").read_text())["kind"] != "driver-control":
            raise RuntimeError("Not a driver control")
    result_path = path if resume is None else path / "resumptions" / uuid.uuid4().hex
    result_path.mkdir(parents=True, exist_ok=True)
    passed = False
    try:
        assert_source(result_path)
        for backend in ([selected] if selected is not None else BACKENDS):
            if backend not in BACKENDS:
                raise ValueError("Unknown local backend")
            select_backend(backend, path)
            directory = path / backend
            if not directory.exists():
                phase("driver-publish:" + backend)
                result = tool(
                    "--mode=publish",
                    "--backend=" + backend,
                    "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
                    "--output=/" + str(directory.relative_to(ROOT)),
                    check=False,
                )
                (path / (backend + "-publisher.log")).write_text(
                    result.stdout + "\n" + result.stderr
                )
                if result.returncode:
                    raise RuntimeError(
                        "Publisher failed; preserve its journal: " + result.stderr
                    )
                fence(directory)
            elif not (directory / "fenced.json").exists():
                raise RuntimeError(
                    "Unfenced prior publication must be reconciled before resume"
                )
            inspect = tool(
                "--mode=inspect",
                "--backend=" + backend,
                "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
                check=False,
            )
            (result_path / (backend + "-native-inspection.log")).write_text(
                inspect.stdout + "\n" + inspect.stderr
            )
            deadline = time.monotonic() + 120
            attempt = 0
            while True:
                attempt += 1
                phase("driver-verify:" + backend)
                (result_path / backend).mkdir(parents=True, exist_ok=True)
                _, missing = verify(
                    backend,
                    path / "fixture.json",
                    directory,
                    (result_path / backend / ("readback-" + str(attempt) + ".json")),
                )
                if not missing:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Acknowledged telemetry remains missing; preserve before recovery"
                    )
                time.sleep(2)
        passed = True
    except BaseException:
        (result_path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        save(
            result_path / "status.json",
            {
                "passed": passed,
                "backends": [selected] if selected is not None else list(BACKENDS),
                "scope": "Small three-signal driver/native mapping control",
            },
        )


def main():
    (ROOT / "results").mkdir(exist_ok=True)
    with (ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        mode = sys.argv[1]
        phase(mode)
        if mode == "docker-up":
            bootstrap()
        elif mode == "up":
            initialize()
        elif mode == "driver-control":
            driver_control()
        elif mode == "resume-control":
            import os

            driver_control(
                os.environ.get("CASE", ""), os.environ.get("BACKEND") or None
            )
        elif mode == "diagnose-projection":
            import os

            run = os.environ.get("CASE", "")
            if len(run) != 32 or any(c not in "0123456789abcdef" for c in run):
                raise ValueError("Invalid preserved case ID")
            case = ROOT / "results/cases" / run
            audit = case / "projection-diagnostics" / uuid.uuid4().hex
            assert_source(audit)
            select_backend("victoria", audit)
            value, missing = verify(
                "victoria",
                case / "fixture.json",
                case / "victoria",
                audit / "projected-readback.json",
                projection=True,
            )
            save(
                audit / "status.json",
                {
                    "all_ack_values_present": not missing,
                    "exact_type_fidelity": False,
                    "scope": "Explicit secondary string-number projection only; strict failed qualification is unchanged",
                },
            )
            if missing:
                raise RuntimeError("ACKs missing even in the explicit value projection")
        elif mode == "test":
            if not any((ROOT / "results/cases").glob("*/case.json")):
                driver_control()
            from experiments import run

            run("healthy")
        elif mode in ["correctness", "collector-crash"]:
            from experiments import run

            import os

            run(
                "healthy" if mode == "correctness" else mode,
                selected=os.environ.get("BACKEND") or None,
            )
        elif mode == "resume-experiment":
            import os
            from experiments import run

            case = os.environ.get("CASE", "")
            if len(case) != 32 or any(c not in "0123456789abcdef" for c in case):
                raise ValueError("Invalid case")
            metadata = json.loads(
                (ROOT / "results/cases" / case / "case.json").read_text()
            )
            run(metadata["kind"], resume=case)
        elif mode == "verify-retained":
            from experiments import audit_retained

            audit_retained()
        elif mode == "down":
            from experiments import audit_retained
            from lifecycle import remove

            remove(audit_retained)
        elif mode == "docker-down":
            from lifecycle import destroy

            destroy()
        elif mode == "status":
            print(command("ps", "--all").stdout)
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    main()
