"""Bounded live native-ingress cases; fault/recovery evidence stays per case."""

from datetime import datetime
import hashlib
import json
import subprocess
import threading
import time
import traceback
import uuid

from control import (
    ROOT,
    PROJECT,
    DOCKER,
    SERVICE,
    command,
    tool,
    owned,
    source_manifest,
    save,
    active_backend,
    retired,
)

ACTORS = ["incident-workload", "incident-publisher", "incident-reader"]


def running_actors():
    return command("ps", "--quiet", *ACTORS).stdout.splitlines()


def events(path):
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raw = raw.rsplit(b"\n", 1)[0] if b"\n" in raw else b""
    return [json.loads(line) for line in raw.splitlines() if line]


def phase(value):
    temporary = ROOT / "results/phase.next"
    temporary.write_text(value)
    temporary.replace(ROOT / "results/phase")


def actor(identifier, role):
    row = json.loads(
        subprocess.check_output([*DOCKER, "inspect", identifier], text=True)
    )[0]
    labels = row["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != role
        or labels.get("com.docker.compose.project.working_dir") != str(ROOT)
    ):
        raise RuntimeError("Foreign actor")
    return row


def stop_actor(identifier, role, directory):
    row = actor(identifier, role)
    if row["State"]["Running"]:
        (directory / "stop").touch()
        subprocess.run(
            [*DOCKER, "wait", identifier], check=True, capture_output=True, timeout=65
        )
        row = actor(identifier, role)
    logs = subprocess.check_output(
        [*DOCKER, "logs", identifier], text=True, stderr=subprocess.STDOUT
    )
    (directory / (role + ".log")).write_text(logs)
    save(directory / (role + "-exit.json"), row)
    if row["State"]["ExitCode"] or row["State"]["OOMKilled"]:
        raise RuntimeError("Actor failed; preserve container/journals: " + row["Name"])
    subprocess.run([*DOCKER, "rm", identifier], check=True, capture_output=True)


def fence(directory):
    if running_actors():
        raise RuntimeError("Cannot fence live actors")
    hashes = {
        s: hashlib.sha256((directory / s / "requests.jsonl").read_bytes()).hexdigest()
        for s in ["metrics", "logs", "traces"]
    }
    proof = directory / "fenced.json"
    if proof.exists():
        if json.loads(proof.read_text())["histories"] != hashes:
            raise RuntimeError("Previously fenced histories changed")
        return
    save(
        proof,
        {"actors_stopped": True, "histories": hashes, "fenced_at_unix_s": time.time()},
    )


def readback(fixture, directory, destination):
    destination.mkdir(parents=True, exist_ok=True)
    report = destination / "readback.json"
    response = tool(
        "--mode=verify",
        "--backend=openobserve",
        "--fixture=/" + str(fixture.relative_to(ROOT)),
        "--output=/" + str(directory.relative_to(ROOT)),
        "--report=/" + str(report.relative_to(ROOT)),
        check=False,
    )
    (destination / "readback.log").write_text(response.stdout + "\n" + response.stderr)
    if response.returncode:
        raise RuntimeError(
            "Uncached native oracle failed; preserve report and original data"
        )
    value = json.loads(report.read_text())
    if not value["common_projection_passed"] or set(value["signals"]) != {
        "metrics",
        "logs",
        "traces",
    }:
        raise RuntimeError("Incomplete logical projection report")
    return value


def disk_snapshot(path, label):
    row = owned(SERVICE)
    volumes = {}
    for mount in row["Mounts"]:
        if mount["Type"] != "volume":
            continue
        metadata = json.loads(
            subprocess.check_output(
                [*DOCKER, "volume", "inspect", mount["Name"]], text=True
            )
        )[0]
        if metadata.get("Labels", {}).get("com.docker.compose.project") != PROJECT:
            raise RuntimeError("Foreign volume")
        response = subprocess.run(
            [
                *DOCKER,
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cpus",
                ".1",
                "--memory",
                "64m",
                "--user",
                "0:0",
                "--label",
                "com.docker.compose.project=" + PROJECT,
                "--label",
                "com.docker.compose.service=disk-observer",
                "--mount",
                "type=volume,source=" + mount["Name"] + ",target=/data,readonly",
                "obs-incident-platforms-workload:local",
                "--mode=disk",
            ],
            text=True,
            capture_output=True,
            timeout=120,
        )
        (path / ("disk-helper-" + label + ".log")).write_text(response.stderr)
        if response.returncode:
            raise RuntimeError("Read-only disk measurement failed")
        volumes[mount["Name"]] = json.loads(response.stdout)
    save(
        path / ("disk-" + label + ".json"),
        {
            "volumes": volumes,
            "allocated_data_bytes": sum(v["allocated_bytes"] for v in volumes.values()),
            "logical_file_bytes": sum(
                v["logical_regular_file_bytes"] for v in volumes.values()
            ),
            "scope": "Read-only sampled allocation including retained fixtures; not continuous peak or codec ratio",
        },
    )


def live_ready(directory, after=None):
    rows = events(directory / "reader/reads.jsonl")
    for signal in ["metrics", "logs", "traces"]:
        for age in ["old", "recent"]:
            candidates = [
                r
                for r in rows
                if r["signal"] == signal
                and r["kind"] == age
                and r["result"] == "visible"
            ]
            if after is not None and age == "recent":
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
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for identifier, role in actors:
            row = actor(identifier, role)
            if not row["State"]["Running"] or row["State"]["Paused"]:
                raise RuntimeError("Actor stopped/paused during live observation")
        if live_ready(directory, after):
            return
        time.sleep(0.5)
    raise TimeoutError(
        "Old/recent visibility did not recover before the declared deadline"
    )


def run(kind):
    if kind not in ["healthy", "ingress-crash"]:
        raise ValueError(kind)
    active_backend()
    if running_actors():
        raise RuntimeError("A live actor prevents a new case")
    controls = [
        p
        for p in (ROOT / "results/cases").glob("*/case.json")
        if not retired(p.parent)
        and json.loads(p.read_text())["kind"] == "driver-control"
        and json.loads(p.read_text()).get("service", "openobserve") == SERVICE
    ]
    if not controls:
        raise RuntimeError("Establish the small driver control first")
    prior = max(controls, key=lambda p: p.stat().st_mtime_ns).parent
    if not any(
        json.loads(p.read_text()).get("common_projection_passed")
        for p in prior.glob("readbacks/*/status.json")
    ):
        raise RuntimeError("The small native projection has not passed")
    retained = []
    for metadata in sorted((ROOT / "results/cases").glob("*/case.json")):
        if (
            retired(metadata.parent)
            or json.loads(metadata.read_text()).get("service", "openobserve") != SERVICE
        ):
            continue
        directory = metadata.parent / "openobserve"
        if not any(directory.glob("*/requests.jsonl")):
            continue
        if not (directory / "fenced.json").exists():
            raise RuntimeError(
                "Unfenced prior publication must be reconciled first: " + str(directory)
            )
        retained.append(metadata.parent)
    identifier = uuid.uuid4().hex
    path = ROOT / "results/cases" / identifier
    fixture = json.loads((prior / "fixture.json").read_text())
    fixture.update(run=identifier, requests=10000)
    save(path / "fixture.json", fixture)
    save(
        path / "case.json",
        {
            "kind": kind,
            "backend": "openobserve",
            "service": SERVICE,
            "backends_planned": ["clickstack", "openobserve", "signoz", "grafana"],
            "prior": str(prior.relative_to(ROOT)),
            "scope": "Shared logical incident projection, not lossless OTLP or a capacity winner",
        },
    )
    source_manifest(path)
    for old in retained:
        readback(
            old / "fixture.json",
            old / "openobserve",
            path / "retained-before" / old.name,
        )
    disk_snapshot(path, "before-ingest")
    directory = path / "openobserve"
    actors = []
    backend = None
    sampler_stop = threading.Event()
    sampler_errors = []

    def sample_disk():
        index = 0
        while not sampler_stop.wait(30):
            index += 1
            try:
                disk_snapshot(path, "sample-" + str(index))
            except Exception as error:
                sampler_errors.append(str(error))
                return

    sampler = threading.Thread(target=sample_disk)
    sampler.start()
    passed = False
    try:
        phase("live-publish:openobserve")
        try:
            for role, mode in [
                ("incident-publisher", "publish"),
                ("incident-reader", "watch"),
            ]:
                if mode == "watch":
                    deadline = time.monotonic() + 20
                    while not (directory / "configuration.json").exists():
                        if time.monotonic() > deadline:
                            raise TimeoutError("Publisher configuration missing")
                        time.sleep(0.1)
                arguments = [
                    "run",
                    "--detach",
                    "--no-deps",
                    "--name",
                    PROJECT + "-" + identifier + "-" + mode,
                    role,
                    "--mode=" + mode,
                    "--backend=openobserve",
                    "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
                    "--output=/" + str(directory.relative_to(ROOT)),
                ]
                if mode == "watch":
                    arguments.append(
                        "--prior-fixture=/"
                        + str((prior / "fixture.json").relative_to(ROOT))
                    )
                response = command(*arguments)
                container = response.stdout.strip().splitlines()[-1]
                actors.append((container, role))
                row = actor(container, role)
                expected = (
                    (750000000, 384 * 2**20)
                    if mode == "publish"
                    else (250000000, 128 * 2**20)
                )
                if (
                    row["HostConfig"]["NanoCpus"],
                    row["HostConfig"]["Memory"],
                ) != expected:
                    raise RuntimeError("Effective workload budget differs")
            wait_ready(directory, actors)
            if kind == "ingress-crash":
                backend = owned(SERVICE)
                began = time.monotonic()
                phase("fault:openobserve:monolith")
                subprocess.run(
                    [*DOCKER, "kill", "--signal", "KILL", backend["Id"]],
                    check=True,
                    capture_output=True,
                )
                killed = owned(SERVICE)
                if (
                    killed["State"]["Running"]
                    or killed["State"]["ExitCode"] != 137
                    or killed["State"]["OOMKilled"]
                ):
                    raise RuntimeError("SIGKILL was not demonstrated")
                save(path / "monolith-killed.json", killed)
                while time.monotonic() - began < 10:
                    for container, role in actors:
                        state = actor(container, role)["State"]
                        if not state["Running"] or state["Paused"]:
                            raise RuntimeError(
                                "Workload actor stopped or paused during held outage"
                            )
                    time.sleep(0.5)
                subprocess.run(
                    [*DOCKER, "start", backend["Id"]], check=True, capture_output=True
                )
                started = owned(SERVICE)["State"]["StartedAt"]
                phase("recovery:openobserve:monolith")
                wait_ready(directory, actors, started)
                save(
                    path / "monolith-recovery.json",
                    {
                        "kill_to_new_ack_visible_s": time.monotonic() - began,
                        "started_at": started,
                        "same_container": owned(SERVICE)["Id"] == backend["Id"],
                        "scope": "Whole native monolith, including reads/UI/storage; not equivalent to a separate collector fault",
                    },
                )
            subprocess.run(
                [*DOCKER, "wait", actors[0][0]],
                check=True,
                capture_output=True,
                timeout=660,
            )
        finally:
            if backend is not None and not owned(SERVICE)["State"]["Running"]:
                subprocess.run(
                    [*DOCKER, "start", backend["Id"]], check=True, capture_output=True
                )
            failures = []
            for container, role in actors:
                try:
                    stop_actor(container, role, directory)
                except Exception as error:
                    failures.append(str(error))
            if failures:
                raise RuntimeError("; ".join(failures))
        summaries = [
            r
            for r in events(directory / "incident-publisher.log")
            if "planned_items" in r
        ]
        if len(summaries) != 3 or any(
            not r["completed"] or r["emitted_items"] != r["planned_items"]
            for r in summaries
        ):
            raise RuntimeError("Publication did not attempt the complete corpus")
        if kind == "healthy" and any(r["unacknowledged_requests"] for r in summaries):
            raise RuntimeError("Healthy input had unacknowledged batches")
        fence(directory)
        phase("fenced-readback:openobserve")
        readback(path / "fixture.json", directory, directory / "final")
        for old in retained:
            readback(
                old / "fixture.json",
                old / "openobserve",
                path / "retained-after" / old.name,
            )
        disk_snapshot(path, "after-actors")
        passed = True
    except BaseException:
        (path / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        sampler_stop.set()
        sampler.join(timeout=150)
        if sampler.is_alive():
            sampler_errors.append("Disk sampler did not stop")
        save(
            path / "status.json",
            {
                "common_projection_passed": passed and not sampler_errors,
                "lossless_otlp_roundtrip": False,
                "measurement_errors": sampler_errors,
                "scope": "Common logical values only; native trace projection limits are unchanged",
            },
        )
    if sampler_errors:
        raise RuntimeError("; ".join(sampler_errors))
    print(path)
