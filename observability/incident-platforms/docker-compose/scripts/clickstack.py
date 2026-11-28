"""Native ClickStack bootstrap; preserve native services, schema and OpAMP."""

import json
import os
from pathlib import Path
import subprocess
import time
import traceback
import uuid

from control import ROOT, DOCKER, command, owned, save, tool, source_manifest

ROLES = {
    "clickstack-clickhouse": (4000000000, 7 * 2**30),
    "clickstack-mongo": (1000000000, 2**30),
    "clickstack-app": (1500000000, 2 * 2**30),
    "clickstack-collector": (1500000000, 2 * 2**30),
}


def native_request(path, *, method="GET", body=None, cookie="", token=""):
    if path not in [
        "/ready",
        "/health",
        "/installation",
        "/register/password",
        "/login/password",
        "/team",
        "/connections",
        "/sources",
        "/collector-health",
        "/collector-logs",
    ]:
        raise ValueError("Unknown native API path")
    url = {
        "/collector-health": "http://clickstack-collector:13133/",
        "/collector-logs": "http://clickstack-collector:4318/v1/logs",
    }.get(path, "http://127.0.0.1:8000" + path)
    script = """
const [url,method,body,cookie,token]=process.argv.slice(1);
const headers={'Content-Type':'application/json'};
if(cookie)headers.Cookie=cookie;
if(token)headers.Authorization=token;
const response=await fetch(url,{method,headers,body:body==='null'?undefined:body,redirect:'manual',signal:AbortSignal.timeout(5000)});
const text=await response.text();
if(text.length>1048576)throw Error('Oversized native API response');
console.log(JSON.stringify({status:response.status,body:text,cookies:response.headers.getSetCookie()}));
"""
    result = command(
        "exec",
        "--no-TTY",
        "clickstack-app",
        "node",
        "--input-type=module",
        "-e",
        script,
        url,
        method,
        json.dumps(body),
        cookie,
        token,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Native API request failed: " + result.stderr[-1000:])
    return json.loads(result.stdout)


def bootstrap():
    if command(
        "ps", "--quiet", "incident-publisher", "incident-reader", "incident-workload"
    ).stdout.strip():
        raise RuntimeError("Live actors prevent switching native platforms")
    for service in ["openobserve", "openobserve-sync"]:
        if command("ps", "--quiet", service).stdout.strip():
            raise RuntimeError(
                "Stop/reconcile the OpenObserve variant before switching"
            )
    directory = ROOT / "results/platforms/clickstack/bootstrap" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    source_manifest(directory)
    configuration = json.loads(
        command("--profile", "clickstack", "config", "--format", "json").stdout
    )
    anonymous = ROOT / ".build/anonymous-docker"
    anonymous.mkdir(parents=True, exist_ok=True)
    (anonymous / "config.json").write_text("{}\n")
    for role in ROLES:
        image = configuration["services"][role]["image"]
        if subprocess.run(
            [*DOCKER, "image", "inspect", image], capture_output=True
        ).returncode:
            with (directory / (role + "-pull.log")).open("w") as log:
                subprocess.run(
                    [DOCKER[0], "--config", str(anonymous), *DOCKER[1:], "pull", image],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=660,
                )
    capacity = json.loads(tool("--mode=filesystem").stdout)
    save(directory / "filesystem.json", capacity)
    if capacity["filesystem_available_bytes"] < 8 * 2**30:
        raise RuntimeError(
            "Fewer than 8 GiB free after artifact download; do not start input"
        )
    configuration = json.loads(
        command("--profile", "clickstack", "config", "--format", "json").stdout
    )
    for role in ROLES:
        for mount in configuration["services"][role].get("volumes", []):
            if mount["type"] == "bind" and not Path(mount["source"]).is_file():
                raise RuntimeError("Missing native bind file: " + mount["source"])
    save(directory / "configuration.json", configuration)
    try:
        command("up", "--detach", "--no-recreate", "--pull", "never", *ROLES)
        snapshots = {}
        for role, budget in ROLES.items():
            row = owned(role)
            if (row["HostConfig"]["NanoCpus"], row["HostConfig"]["Memory"]) != budget:
                raise RuntimeError("Effective native role budget differs: " + role)
            if row["Config"]["Image"] != configuration["services"][role]["image"]:
                raise RuntimeError("Existing native image differs: " + role)
            snapshots[role] = {
                "id": row["Id"],
                "image": row["Image"],
                "reference": row["Config"]["Image"],
                "budget": budget,
                "mounts": row["Mounts"],
            }
        save(directory / "effective-roles.json", snapshots)
        until = time.monotonic() + 90
        while time.monotonic() < until:
            version = command(
                "exec",
                "--no-TTY",
                "clickstack-clickhouse",
                "clickhouse-client",
                "--user",
                "lab",
                "--password",
                "Local-clickstack-only",
                "--query",
                "SELECT version() FORMAT JSONEachRow",
                check=False,
            )
            if version.returncode == 0:
                (directory / "clickhouse-version.jsonl").write_text(version.stdout)
                break
            if not owned("clickstack-clickhouse")["State"]["Running"]:
                raise RuntimeError("Native ClickHouse exited during startup")
            time.sleep(1)
        else:
            raise TimeoutError("Native ClickHouse did not become ready")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                ready = native_request("/ready")
                if ready["status"] == 200:
                    save(directory / "native-ready.json", ready)
                    break
            except (RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(2)
        else:
            raise TimeoutError("Native app readiness/MongoDB connection did not pass")
        installation = native_request("/installation")
        save(directory / "installation-before.json", installation)
        if installation["status"] != 200:
            raise RuntimeError("Native installation API failed")
        exists = json.loads(installation["body"])["isTeamExisting"]
        credentials = {
            "email": "incident@example.test",
            "password": "Local-Incident-P4ssword!",
        }
        if not exists:
            credentials["confirmPassword"] = credentials["password"]
        response = native_request(
            "/login/password" if exists else "/register/password",
            method="POST",
            body=credentials,
        )
        save(directory / "native-auth.json", response)
        if response["status"] not in [200, 302, 303] or not response["cookies"]:
            raise RuntimeError("Native registration/login did not establish a session")
        cookie = "; ".join(value.split(";", 1)[0] for value in response["cookies"])
        for path in ["/team", "/connections", "/sources"]:
            value = native_request(path, cookie=cookie)
            save(directory / (path[1:] + ".json"), value)
            if value["status"] != 200:
                raise RuntimeError("Native authenticated API failed: " + path)
        version = command(
            "exec",
            "--no-TTY",
            "clickstack-clickhouse",
            "clickhouse-client",
            "--user",
            "lab",
            "--password",
            "Local-clickstack-only",
            "--query",
            "SELECT version() FORMAT JSONEachRow",
        )
        (directory / "clickhouse-version.jsonl").write_text(version.stdout)
        mongo = command(
            "exec",
            "--no-TTY",
            "clickstack-mongo",
            "mongosh",
            "--quiet",
            "--eval",
            "JSON.stringify({version:db.version(),ping:db.adminCommand({ping:1})})",
        )
        (directory / "mongo-version.json").write_text(mongo.stdout)
        health = native_request("/collector-health")
        save(directory / "collector-health.json", health)
        if health["status"] != 200:
            raise RuntimeError("Native collector health failed")
        unauthorized = native_request("/collector-logs", method="POST", body={})
        authorized = native_request(
            "/collector-logs",
            method="POST",
            body={},
            token="local-incident-ingestion-token-only",
        )
        save(
            directory / "ingress-auth-probes.json",
            {
                "unauthenticated": unauthorized,
                "authenticated_empty_otlp_json": authorized,
                "scope": "Empty requests only, no workload corpus or durability assertion",
            },
        )
        if unauthorized["status"] not in [401, 403] or authorized["status"] != 200:
            raise RuntimeError(
                "Native ingestion authentication did not match its contract"
            )
        tables = command(
            "exec",
            "--no-TTY",
            "clickstack-clickhouse",
            "clickhouse-client",
            "--user",
            "lab",
            "--password",
            "Local-clickstack-only",
            "--query",
            "SHOW TABLES FROM default FORMAT JSONEachRow",
        )
        (directory / "native-tables.jsonl").write_text(tables.stdout)
        save(
            directory / "status.json",
            {
                "native_metadata_bootstrap_passed": True,
                "workload_qualified": False,
                "scope": "Native app/readiness/Mongo CRUD/source defaults only; collector admission/schema and full corpus are separate gates",
            },
        )
        print(directory)
    except BaseException:
        (directory / "failure.txt").write_text(traceback.format_exc())
        save(
            directory / "status.json",
            {"native_metadata_bootstrap_passed": False, "workload_qualified": False},
        )
        raise
    finally:
        for role in ROLES:
            if command("ps", "--all", "--quiet", role).stdout.strip():
                row = owned(role)
                save(directory / (role + "-state.json"), row["State"])
                logs = subprocess.run(
                    [*DOCKER, "logs", row["Id"]],
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                (directory / (role + ".log")).write_text(
                    logs.stdout + "\n" + logs.stderr
                )


def active():
    if command("ps", "--quiet", "openobserve", "openobserve-sync").stdout.strip():
        raise RuntimeError("Competing platform is active")
    configuration = json.loads(
        command("--profile", "clickstack", "config", "--format", "json").stdout
    )
    for role, budget in ROLES.items():
        row = owned(role)
        if (
            not row["State"]["Running"]
            or row["State"]["Paused"]
            or (row["HostConfig"]["NanoCpus"], row["HostConfig"]["Memory"]) != budget
            or row["Config"]["Image"] != configuration["services"][role]["image"]
        ):
            raise RuntimeError(
                "Effective native topology/image/budget differs: " + role
            )


def driver_control():
    if command(
        "ps",
        "--quiet",
        "openobserve",
        "openobserve-sync",
        "incident-publisher",
        "incident-reader",
        "incident-workload",
    ).stdout.strip():
        raise RuntimeError("Another platform or workload is active")
    for role, budget in ROLES.items():
        row = owned(role)
        if (
            not row["State"]["Running"]
            or (row["HostConfig"]["NanoCpus"], row["HostConfig"]["Memory"]) != budget
        ):
            raise RuntimeError("Native ClickStack budget or running topology differs")
    for metadata in (ROOT / "results/cases").glob("*/case.json"):
        if json.loads(metadata.read_text())["backend"] == "clickstack":
            raise RuntimeError(
                "Existing ClickStack input must be reconciled before a new driver control"
            )
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
            "backend": "clickstack",
            "service": "clickstack",
            "qualification": "pending native full-value oracle",
        },
    )
    save(path / "fixture.json", fixture)
    source_manifest(path)
    schema = tool("--mode=schema", "--backend=clickstack")
    (path / "native-schema.json").write_text(schema.stdout)
    response = tool(
        "--mode=publish",
        "--backend=clickstack",
        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
        "--output=/" + str((path / "clickstack").relative_to(ROOT)),
        check=False,
    )
    (path / "publisher.log").write_text(response.stdout + "\n" + response.stderr)
    summaries = [
        json.loads(line) for line in response.stdout.splitlines() if line.strip()
    ]
    if (
        response.returncode
        or len(summaries) != 3
        or any(
            not row["completed"] or row["unacknowledged_requests"] for row in summaries
        )
    ):
        raise RuntimeError("Small native admission failed; preserve original histories")
    from experiments import fence

    fence(path / "clickstack")
    deadline = time.monotonic() + 30
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        response = tool(
            "--mode=sample",
            "--backend=clickstack",
            "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
            check=False,
        )
        (path / ("sample-" + str(attempt) + ".jsonl")).write_text(response.stdout)
        (path / ("sample-" + str(attempt) + ".log")).write_text(response.stderr)
        rows = [
            json.loads(line) for line in response.stdout.splitlines() if line.strip()
        ]
        if (
            not response.returncode
            and len(rows) == 3
            and all(row["rows"] for row in rows)
        ):
            save(
                path / "status.json",
                {
                    "source_admission_passed": True,
                    "all_signal_samples_visible": True,
                    "qualified": False,
                    "scope": "Small native samples only; full values and crash survival not qualified",
                },
            )
            print(path)
            return
        time.sleep(1)
    raise TimeoutError("Small native samples did not become visible; preserve input")


def verify_control():
    if command(
        "ps",
        "--quiet",
        "openobserve",
        "openobserve-sync",
        "incident-publisher",
        "incident-reader",
        "incident-workload",
    ).stdout.strip():
        raise RuntimeError(
            "A competing platform or live actor prevents frozen native readback"
        )
    for role, budget in ROLES.items():
        row = owned(role)
        if (
            not row["State"]["Running"]
            or (row["HostConfig"]["NanoCpus"], row["HostConfig"]["Memory"]) != budget
        ):
            raise RuntimeError("Effective native topology/budget differs: " + role)
    identifier = os.environ.get("CASE", "")
    if not identifier:
        controls = [
            p.parent
            for p in (ROOT / "results/cases").glob("*/case.json")
            if json.loads(p.read_text())["backend"] == "clickstack"
            and json.loads(p.read_text())["kind"] == "driver-control"
        ]
        if not controls:
            raise RuntimeError("Create the small driver control first")
        identifier = max(controls, key=lambda p: p.stat().st_mtime_ns).name
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid preserved case ID")
    path = ROOT / "results/cases" / identifier
    if json.loads((path / "case.json").read_text())["backend"] != "clickstack":
        raise RuntimeError("Wrong native backend for case")
    if not (path / "clickstack/fenced.json").exists():
        raise RuntimeError("Investigate an unfenced publication before readback")
    from experiments import fence

    fence(path / "clickstack")
    destination = path / "readbacks" / uuid.uuid4().hex
    source_manifest(destination)
    report = destination / "readback.json"
    result = tool(
        "--mode=verify",
        "--backend=clickstack",
        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
        "--output=/" + str((path / "clickstack").relative_to(ROOT)),
        "--report=/" + str(report.relative_to(ROOT)),
        check=False,
    )
    (destination / "readback.log").write_text(result.stdout + "\n" + result.stderr)
    save(
        destination / "status.json",
        {
            "common_projection_passed": result.returncode == 0,
            "lossless_otlp_roundtrip": False,
        },
    )
    if result.returncode:
        raise RuntimeError(
            "Native full-value oracle failed; preserve original data/report"
        )
    print(destination)


def remove():
    from experiments import running_actors, readback, fence
    from lifecycle import witnesses

    active()
    if running_actors():
        raise RuntimeError("Live actors prevent teardown")
    paths = [
        p.parent
        for p in (ROOT / "results/cases").glob("*/case.json")
        if json.loads(p.read_text())["backend"] == "clickstack"
    ]
    for path in paths:
        if not any((path / "clickstack").glob("*/requests.jsonl")):
            continue
        status = path / "status.json"
        if (
            status.exists()
            and json.loads(status.read_text()).get("common_projection_passed") is False
        ):
            raise RuntimeError(
                "Known failed ClickStack case blocks destruction; preserve/recover "
                + path.name
            )
    destination = ROOT / "results/teardown-audits" / uuid.uuid4().hex
    for path in paths:
        directory = path / "clickstack"
        if not any(directory.glob("*/requests.jsonl")):
            continue
        if not (directory / "fenced.json").exists():
            raise RuntimeError("Unfenced native input must be investigated")
        fence(directory)
        readback(
            path / "fixture.json", directory, destination / path.name, "clickstack"
        )
    command("stop", *ROLES)
    rows = {role: owned(role) for role in ROLES}
    save(
        ROOT / "results/verified-down/clickstack.json",
        {
            "verified": True,
            "containers": {role: row["Id"] for role, row in rows.items()},
            "witnesses": witnesses(),
            "scope": "All native acknowledged values reconciled before stopping; data retained until project-wide verified destruction",
        },
    )


def preserve_stop():
    from experiments import running_actors
    from lifecycle import witnesses

    if running_actors():
        raise RuntimeError("Live actors must stop before preserving native services")
    for role in ROLES:
        owned(role)
    command("stop", *ROLES)
    rows = {role: owned(role) for role in ROLES}
    save(
        ROOT / "results/preserved-stops" / ("clickstack-" + uuid.uuid4().hex + ".json"),
        {
            "verified_for_destruction": False,
            "services": {
                role: {"id": row["Id"], "state": row["State"], "mounts": row["Mounts"]}
                for role, row in rows.items()
            },
            "witnesses": witnesses(),
            "scope": "Native failed data and all volumes retained; not a deletion receipt",
        },
    )


def execute(mode):
    if mode == "bootstrap":
        bootstrap()
    elif mode == "up":
        if not any(
            json.loads(p.read_text())["backend"] == "clickstack"
            for p in (ROOT / "results/cases").glob("*/case.json")
        ):
            driver_control()
        verify_control()
    elif mode == "down":
        remove()
    elif mode == "preserve-stop":
        preserve_stop()
    elif mode == "docker-down":
        from lifecycle import destroy

        destroy()
    elif mode in ["full", "ingress-crash"]:
        from experiments import run

        run("healthy" if mode == "full" else mode, "clickstack")
    elif mode == "driver-control":
        driver_control()
    elif mode in ["verify-control", "test"]:
        verify_control()
    elif mode == "status":
        print(command("ps", "--all", *ROLES).stdout)
    else:
        raise RuntimeError(
            "ClickStack native bootstrap is in progress; this lifecycle case is not implemented: "
            + mode
        )
