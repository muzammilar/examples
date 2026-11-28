"""Native Foundry-derived local SigNoz bootstrap; no workload qualification implied."""

import json
import os
from pathlib import Path
import subprocess
import time
import traceback
import uuid

from control import (
    ROOT,
    DOCKER,
    ALL_BACKENDS,
    command,
    owned,
    save,
    source_manifest,
    tool,
)

ROLES = {
    "signoz-clickhouse": (4000000000, 7 * 2**30),
    "signoz-keeper": (500000000, 512 * 2**20),
    "signoz-postgres": (500000000, 512 * 2**20),
    "signoz-app": (1500000000, 2 * 2**30),
    "signoz-collector": (1500000000, 2 * 2**30),
}
BASE = ["signoz-keeper", "signoz-clickhouse", "signoz-postgres"]


def wait_for(description, probe, timeout=120):
    until = time.monotonic() + timeout
    last = ""
    while time.monotonic() < until:
        result = probe()
        if result.returncode == 0:
            return result
        last = result.stdout + "\n" + result.stderr
        time.sleep(1)
    raise TimeoutError(description + " did not become ready: " + last[-2000:])


def bootstrap():
    if command(
        "ps", "--quiet", "incident-publisher", "incident-reader", "incident-workload"
    ).stdout.strip():
        raise RuntimeError("Live actors prevent changing platform")
    for role in ALL_BACKENDS:
        if role not in ROLES and command("ps", "--quiet", role).stdout.strip():
            raise RuntimeError(
                "Stop/preserve the previous native platform first: " + role
            )
    path = ROOT / "results/platforms/signoz/bootstrap" / uuid.uuid4().hex
    path.mkdir(parents=True)
    source_manifest(path)
    configuration = json.loads(
        command("--profile", "signoz", "config", "--format", "json").stdout
    )
    save(path / "configuration.json", configuration)
    for role in ROLES:
        for mount in configuration["services"][role].get("volumes", []):
            if mount["type"] == "bind" and not Path(mount["source"]).is_file():
                raise RuntimeError("Missing native bind file: " + mount["source"])
    try:
        anonymous = ROOT / ".build/anonymous-docker"
        anonymous.mkdir(parents=True, exist_ok=True)
        (anonymous / "config.json").write_text("{}\n")
        for role in ROLES:
            if role == "signoz-clickhouse":
                continue
            image = configuration["services"][role]["image"]
            if subprocess.run(
                [*DOCKER, "image", "inspect", image], capture_output=True
            ).returncode:
                with (path / (role + "-pull.log")).open("w") as log:
                    subprocess.run(
                        [
                            DOCKER[0],
                            "--config",
                            str(anonymous),
                            *DOCKER[1:],
                            "pull",
                            image,
                        ],
                        check=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=660,
                    )
        build = command("build", "signoz-clickhouse")
        (path / "helper-build.log").write_text(build.stdout + "\n" + build.stderr)
        capacity = json.loads(tool("--mode=filesystem").stdout)
        save(path / "filesystem.json", capacity)
        if capacity["filesystem_available_bytes"] < 8 * 2**30:
            raise RuntimeError("Fewer than 8 GiB free; do not start native input")
        command("up", "--detach", "--no-recreate", "--pull", "never", *BASE)
        version = wait_for(
            "Native ClickHouse",
            lambda: command(
                "exec",
                "--no-TTY",
                "signoz-clickhouse",
                "clickhouse-client",
                "--password",
                "Local-Signoz-only",
                "--query",
                "SELECT version() FORMAT JSONEachRow",
                check=False,
            ),
        )
        (path / "clickhouse-version.jsonl").write_text(version.stdout)
        wait_for(
            "Native PostgreSQL",
            lambda: command(
                "exec",
                "--no-TTY",
                "signoz-postgres",
                "pg_isready",
                "--username",
                "signoz",
                "--dbname",
                "signoz",
                check=False,
            ),
        )
        pg = command(
            "exec",
            "--no-TTY",
            "signoz-postgres",
            "psql",
            "--username",
            "signoz",
            "--dbname",
            "signoz",
            "--tuples-only",
            "--command",
            "SHOW server_version; SHOW fsync; SHOW synchronous_commit;",
        )
        (path / "postgres-settings.txt").write_text(pg.stdout)
        keeper = wait_for(
            "Native Keeper",
            lambda: command(
                "exec",
                "--no-TTY",
                "signoz-keeper",
                "clickhouse-keeper-client",
                "--host",
                "localhost",
                "--port",
                "9181",
                "--query",
                "ls",
                check=False,
            ),
        )
        (path / "keeper-ready.txt").write_text(keeper.stdout)
        migration = command(
            "run",
            "--no-deps",
            "--name",
            "obs-incident-platforms-signoz-migrator-" + path.name,
            "signoz-migrator",
            check=False,
        )
        (path / "migration.log").write_text(migration.stdout + "\n" + migration.stderr)
        if migration.returncode:
            raise RuntimeError(
                "Native schema migration failed; retain all native state and migrator container"
            )
        command("up", "--detach", "--no-recreate", "--pull", "never", "signoz-app")
        health = wait_for(
            "Native SigNoz app",
            lambda: command(
                "exec",
                "--no-TTY",
                "signoz-app",
                "wget",
                "--quiet",
                "--output-document=-",
                "http://localhost:8080/api/v1/health",
                check=False,
            ),
        )
        (path / "app-health.json").write_text(health.stdout)
        command(
            "up", "--detach", "--no-recreate", "--pull", "never", "signoz-collector"
        )
        wait_for(
            "Native collector",
            lambda: command(
                "exec",
                "--no-TTY",
                "signoz-app",
                "wget",
                "--quiet",
                "--output-document=-",
                "http://signoz-collector:13133",
                check=False,
            ),
        )
        effective = {}
        for role, budget in ROLES.items():
            row = owned(role)
            if (
                not row["State"]["Running"]
                or (row["HostConfig"]["NanoCpus"], row["HostConfig"]["Memory"])
                != budget
            ):
                raise RuntimeError("Effective native budget/topology differs: " + role)
            effective[role] = {
                "id": row["Id"],
                "image": row["Image"],
                "reference": row["Config"]["Image"],
                "mounts": row["Mounts"],
                "budget": budget,
            }
        save(path / "effective-roles.json", effective)
        tables = command(
            "exec",
            "--no-TTY",
            "signoz-clickhouse",
            "clickhouse-client",
            "--password",
            "Local-Signoz-only",
            "--query",
            "SELECT database,name,engine FROM system.tables WHERE database LIKE 'signoz_%' ORDER BY database,name FORMAT JSONEachRow",
        )
        (path / "native-tables.jsonl").write_text(tables.stdout)
        save(
            path / "status.json",
            {
                "native_bootstrap_passed": True,
                "workload_qualified": False,
                "scope": "Native migration/services only; no corpus, independent value oracle or crash qualification yet",
            },
        )
        print(path)
    except BaseException:
        (path / "failure.txt").write_text(traceback.format_exc())
        save(
            path / "status.json",
            {"native_bootstrap_passed": False, "workload_qualified": False},
        )
        raise
    finally:
        for role in ROLES:
            if command("ps", "--all", "--quiet", role).stdout.strip():
                row = owned(role)
                save(path / (role + "-state.json"), row["State"])
                logs = subprocess.run(
                    [*DOCKER, "logs", row["Id"]],
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                (path / (role + ".log")).write_text(logs.stdout + "\n" + logs.stderr)


def active():
    for rival in ALL_BACKENDS:
        if rival not in ROLES and command("ps", "--quiet", rival).stdout.strip():
            raise RuntimeError("A competing native platform is active")
    for role, budget in ROLES.items():
        row = owned(role)
        if (
            not row["State"]["Running"]
            or row["State"]["Paused"]
            or (row["HostConfig"]["NanoCpus"], row["HostConfig"]["Memory"]) != budget
        ):
            raise RuntimeError("Effective native topology/budget differs: " + role)


def driver_control():
    active()
    if command(
        "ps", "--quiet", "incident-workload", "incident-publisher", "incident-reader"
    ).stdout.strip():
        raise RuntimeError("A live actor prevents input")
    if any(
        json.loads(p.read_text())["backend"] == "signoz"
        for p in (ROOT / "results/cases").glob("*/case.json")
    ):
        raise RuntimeError("Prior native input must be reconciled first")
    identifier = uuid.uuid4().hex
    path = ROOT / "results/cases" / identifier
    fixture = {
        "run": identifier,
        "payload_seed": "b4587bd2c2314cc7a7c4062660ad874c",
        "start_ms": int(time.time() - 7200) * 1000,
        "requests": 100,
        "logs_per_request": 100,
        "metric_points": 100,
    }
    save(
        path / "case.json",
        {
            "kind": "driver-control",
            "backend": "signoz",
            "service": "signoz",
            "qualification": "pending full native-value oracle",
        },
    )
    save(path / "fixture.json", fixture)
    source_manifest(path)
    result = tool(
        "--mode=publish",
        "--backend=signoz",
        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
        "--output=/" + str((path / "signoz").relative_to(ROOT)),
        check=False,
    )
    (path / "publisher.log").write_text(result.stdout + "\n" + result.stderr)
    summaries = [
        json.loads(line) for line in result.stdout.splitlines() if line.strip()
    ]
    if (
        result.returncode
        or len(summaries) != 3
        or any(
            not row["completed"] or row["unacknowledged_requests"] for row in summaries
        )
    ):
        raise RuntimeError("Native admission failed; preserve original input")
    from experiments import fence

    fence(path / "signoz")
    until = time.monotonic() + 30
    attempt = 0
    while time.monotonic() < until:
        attempt += 1
        sample = tool(
            "--mode=sample",
            "--backend=signoz",
            "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
            check=False,
        )
        (path / ("sample-" + str(attempt) + ".jsonl")).write_text(sample.stdout)
        (path / ("sample-" + str(attempt) + ".log")).write_text(sample.stderr)
        rows = [json.loads(line) for line in sample.stdout.splitlines() if line.strip()]
        if (
            sample.returncode == 0
            and len(rows) == 4
            and all(row["rows"] for row in rows)
        ):
            save(
                path / "status.json",
                {
                    "source_admission_passed": True,
                    "native_samples_visible": True,
                    "qualified": False,
                    "scope": "Small source admission/samples only; independent full values and recovery unqualified",
                },
            )
            print(path)
            return
        time.sleep(1)
    raise TimeoutError("Native samples did not become visible; preserve original input")


def verify_control():
    from experiments import fence, readback, running_actors

    active()
    if running_actors():
        raise RuntimeError("Live actors prevent frozen native readback")
    identifier = os.environ.get("CASE", "")
    if not identifier:
        controls = [
            p.parent
            for p in (ROOT / "results/cases").glob("*/case.json")
            if json.loads(p.read_text())["backend"] == "signoz"
            and json.loads(p.read_text())["kind"] == "driver-control"
        ]
        if not controls:
            raise RuntimeError("Create the small native control first")
        identifier = max(controls, key=lambda p: p.stat().st_mtime_ns).name
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid preserved case ID")
    path = ROOT / "results/cases" / identifier
    if json.loads((path / "case.json").read_text())["backend"] != "signoz":
        raise RuntimeError("Wrong native backend")
    directory = path / "signoz"
    if not (directory / "fenced.json").exists():
        raise RuntimeError("Unfenced source requires investigation")
    fence(directory)
    destination = path / "readbacks" / uuid.uuid4().hex
    source_manifest(destination)
    passed = False
    try:
        readback(path / "fixture.json", directory, destination, "signoz")
        passed = True
        print(destination)
    finally:
        save(
            destination / "status.json",
            {"common_projection_passed": passed, "lossless_otlp_roundtrip": False},
        )


def preserved_cases():
    return [
        path.parent
        for path in sorted((ROOT / "results/cases").glob("*/case.json"))
        if json.loads(path.read_text())["backend"] == "signoz"
    ]


def preserve_stop():
    from experiments import running_actors
    from lifecycle import witnesses

    if running_actors():
        raise RuntimeError("Live actors prevent stopping native storage")
    for role in ROLES:
        owned(role)
    command("stop", *ROLES)
    save(
        ROOT / "results/preserved-stops" / ("signoz-" + uuid.uuid4().hex + ".json"),
        {
            "verified_for_destruction": False,
            "services": {
                role: {key: row[key] for key in ["Id", "State", "Mounts"]}
                for role in ROLES
                for row in [owned(role)]
            },
            "witnesses": witnesses(),
            "scope": "Services stopped; original volumes and failed evidence retained",
        },
    )


def remove():
    from experiments import fence, readback, running_actors

    active()
    if running_actors():
        raise RuntimeError("Live actors prevent teardown")
    paths = preserved_cases()
    for path in paths:
        status = path / "status.json"
        if (
            status.exists()
            and json.loads(status.read_text()).get("common_projection_passed") is False
        ):
            raise RuntimeError(
                "Known failed SigNoz case must be preserved: " + path.name
            )
    destination = ROOT / "results/teardown-audits" / uuid.uuid4().hex
    for path in paths:
        directory = path / "signoz"
        if not any(directory.glob("*/requests.jsonl")):
            continue
        if not (directory / "fenced.json").exists():
            raise RuntimeError("Unfenced native input must be investigated")
        fence(directory)
        readback(path / "fixture.json", directory, destination / path.name, "signoz")
    preserve_stop()


def execute(mode):
    if mode == "bootstrap":
        bootstrap()
    elif mode == "up":
        if not preserved_cases():
            driver_control()
        verify_control()
    elif mode == "down":
        remove()
    elif mode == "preserve-stop":
        preserve_stop()
    elif mode == "docker-down":
        raise RuntimeError("Destruction is unqualified; preserve native SigNoz data")
    elif mode in ["full", "ingress-crash"]:
        from experiments import run

        run("healthy" if mode == "full" else mode, "signoz")
    elif mode in ["verify-control", "test"]:
        verify_control()
    elif mode == "driver-control":
        driver_control()
    elif mode == "status":
        print(command("ps", "--all", *ROLES).stdout)
    else:
        raise RuntimeError(
            "SigNoz bootstrap in progress; lifecycle case not implemented: " + mode
        )
