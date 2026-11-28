"""Example-owned lifecycle. Preserve failed fixtures and source ACKs until exported."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import lab
import pipeline as p
from render_sql import render

STATE = lab.ROOT / "results/fixture.json"


def storage(path):
    names = lab.output(
        "docker",
        "volume",
        "ls",
        "--filter",
        "label=com.docker.compose.project=" + lab.PROJECT,
        "--quiet",
    ).split()
    mounts = []
    for name in names:
        row = json.loads(lab.output("docker", "volume", "inspect", name))[0]
        if row["Labels"].get("com.docker.compose.project") != lab.PROJECT:
            raise ValueError("Unowned volume")
        mounts += [
            "--mount",
            f"type=volume,source={name},target=/storage/{name},readonly",
        ]
    if mounts:
        raw = lab.output(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "0",
            "--cpus",
            ".1",
            "--memory",
            "128m",
            *mounts,
            "--entrypoint",
            "/opt/venv/bin/python",
            "examples/orders-lakehouse-oracle:0.12.0",
            "/app/volume_usage.py",
            timeout=120,
        )
        (path / ("volumes-" + str(time.time_ns()) + ".json")).write_text(raw)


def preserve(path):
    """Copy histories before any owned volume removal; failed export blocks teardown."""
    ids = lab.compose("ps", "--all", "--quiet", "jobmanager").split()
    if len(ids) != 1:
        raise ValueError("Need the retained manager to export checkpoints/history")
    lab.owned(ids[0], "jobmanager")
    for volume, target in (("witness", "witness"), ("spark-state", "spark-events")):
        volume_name = lab.PROJECT + "_" + volume
        volume_state = json.loads(
            lab.output("docker", "volume", "inspect", volume_name)
        )[0]
        if volume_state["Labels"].get("com.docker.compose.project") != lab.PROJECT:
            raise ValueError(
                "Missing/unowned export volume; refuse to create empty replacement state"
            )
        # A temporary read-only mount is explicit, owned and carries no network credentials.
        name = lab.PROJECT + "-export-" + uuid.uuid4().hex
        lab.output(
            "docker",
            "create",
            "--name",
            name,
            "--network",
            "none",
            "--label",
            "com.docker.compose.project=" + lab.PROJECT,
            "--mount",
            f"type=volume,source={volume_name},target=/export,readonly",
            "--entrypoint",
            "/bin/true",
            "examples/orders-lakehouse-oracle:0.12.0",
        )
        try:
            folder = path / target
            folder.mkdir()
            lab.output("docker", "cp", name + ":/export/.", str(folder), timeout=180)
            files = [f for f in folder.rglob("*") if f.is_file()]
            lab.save(
                path / (target + "-hashes.json"),
                {
                    str(f.relative_to(folder)): hashlib.sha256(
                        f.read_bytes()
                    ).hexdigest()
                    for f in files
                },
            )
            if volume == "witness" and not (folder / "baseline.jsonl").is_file():
                raise ValueError("Baseline witness export missing")
        finally:
            lab.output("docker", "rm", name)


def save_status(path, **values):
    row = json.loads((path / "status.json").read_text())
    lab.save(path / "status.json", dict(row, **values, ended_ns=time.time_ns()))


def export_candidates(path, uri, retention_file):
    destination = path / "orphan-files"
    destination.mkdir()
    lab.actor(
        path,
        "lake-oracle",
        ["/app/export_candidates.py", uri, "/app/retention.json"],
        extra=(
            "--entrypoint",
            "/opt/venv/bin/python",
            "--volume",
            str(retention_file.resolve()) + ":/app/retention.json:ro",
            "--volume",
            str(destination.resolve()) + ":/exports",
        ),
        timeout=180,
    )
    receipt = json.loads((destination / "receipts.json").read_text())
    for item in receipt:
        value = (destination / item["file"]).read_bytes()
        if (
            len(value) != item["bytes"]
            or hashlib.sha256(value).hexdigest() != item["sha256"]
        ):
            raise ValueError("Exported candidate did not survive host readback")
    lab.save(
        path / "candidate-export.json",
        dict(
            files=len(receipt),
            bytes=sum(r["bytes"] for r in receipt),
            host_sha256_verified=True,
        ),
    )


def require_fixture():
    state = json.loads(STATE.read_text())
    if state["rows"] != int(os.environ.get("ORDER_COUNT", "1000000")) or state[
        "customers"
    ] != int(os.environ.get("CUSTOMER_COUNT", "10000")):
        raise ValueError(
            "Fixture corpus changed; refusing to rewrite acknowledged data"
        )
    return state


def ready(path):
    p.wait(lambda: len(p.rest("taskmanagers")["taskmanagers"]) == 2, 240)
    p.wait(lambda: p.control(path, "setup-kafka"), 180)
    p.wait(lambda: p.control(path, "setup-bucket"), 180)
    lab.save(path / "catalog-settings.json", p.control(path, "settings"))
    lab.save(path / "flink-version.json", p.rest("config"))


def main(mode, path):
    if mode == "docker-up":
        render()
        config = json.loads(
            lab.compose("--profile", "tools", "config", "--format", "json")
        )
        info = json.loads(lab.output("docker", "info", "--format", "{{json .}}"))
        cpus = sum(float(s["cpus"]) for s in config["services"].values())
        memory = sum(int(s["mem_limit"]) for s in config["services"].values())
        if cpus > info["NCPU"] or memory + 2 * 1024**3 > info["MemTotal"]:
            raise ValueError(
                "Combined all-profile resource budget exceeds Docker headroom"
            )
        if STATE.exists():
            retained = require_fixture()
            if retained["cpu_caps"] != cpus or retained["memory_caps"] != memory:
                retained.setdefault("budget_revisions", []).append(
                    dict(
                        previous_cpu_caps=retained["cpu_caps"],
                        previous_memory_caps=retained["memory_caps"],
                        at_ns=time.time_ns(),
                    )
                )
                retained.update(cpu_caps=cpus, memory_caps=memory)
                lab.save(STATE, retained)
        else:
            if lab.compose("ps", "--all", "--quiet").strip():
                raise ValueError("Containers without owned fixture; refuse adoption")
            lab.save(
                STATE,
                dict(
                    id=uuid.uuid4().hex,
                    rows=int(os.environ.get("ORDER_COUNT", "1000000")),
                    customers=int(os.environ.get("CUSTOMER_COUNT", "10000")),
                    phases=[],
                    cpu_caps=cpus,
                    memory_caps=memory,
                    docker_cpus=info["NCPU"],
                    docker_memory=info["MemTotal"],
                    kernel=info["KernelVersion"],
                    architecture=info["Architecture"],
                    local_only=True,
                    disk_class="Docker Desktop VM / shared host device",
                    failure_domains="one VM; simulated zones",
                ),
            )
        lab.save(path / "fixture.json", require_fixture())
        (path / "docker-info.json").write_text(
            json.dumps(
                {
                    k: info.get(k)
                    for k in (
                        "NCPU",
                        "MemTotal",
                        "Architecture",
                        "KernelVersion",
                        "Driver",
                        "ServerVersion",
                        "OperatingSystem",
                    )
                }
            )
        )
        with (path / "build.log").open("x") as log:
            subprocess.run(
                [*lab.COMPOSE, "--profile", "tools", "build"],
                cwd=lab.ROOT,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=3600,
                env=dict(os.environ, COMPOSE_PARALLEL_LIMIT="1"),
            )
        with lab.Observer(path):
            lab.compose("up", "--detach", *lab.ROLES, timeout=300)
            ready(path)
            lab.save(
                path / "effective-images.json",
                [
                    {
                        "role": lab.owned(i)["Config"]["Labels"][
                            "com.docker.compose.service"
                        ],
                        "image": lab.owned(i)["Image"],
                    }
                    for i in lab.compose("ps", "--quiet").split()
                ],
            )
            storage(path)
        return
    fixture = require_fixture()
    if mode == "up":
        with lab.Observer(path):
            ready(path)
            if fixture["phases"]:
                lab.save(
                    path / "retained-verify.json", p.verify(path, fixture["phases"])
                )
                return
            # A partially completed producer is not re-run: O_EXCL witness fails closed.
            if not p.job():
                existing = [
                    job
                    for job in p.rest("jobs/overview")["jobs"]
                    if job["name"] == "orders-lakehouse"
                ]
                if existing:
                    raise ValueError(
                        "A prior submission exists; inspect/recover it rather than submit a duplicate"
                    )
                result = lab.actor(path, "sql-client", [], timeout=180)
                text = result.read_text()
                if "[ERROR]" in text or "Exception" in text:
                    raise ValueError(
                        "SQL client reported an error despite possible zero exit"
                    )
            p.wait(p.job, 180)
            lab.actor(
                path, "orders", ["baseline", "1", str(fixture["rows"])], timeout=660
            )
            fixture["phases"] = ["baseline"]
            # Record that source ACKs exist even if downstream settlement fails.
            lab.save(STATE, fixture)
            lab.save(path / "baseline.json", p.verify(path, fixture["phases"]))
            storage(path)
        return
    if mode in ("test", "verify"):
        with lab.Observer(path):
            lab.save(
                path / "verification.json",
                p.verify(path, fixture["phases"], replicas=True),
            )
            lab.save(path / "kafka.json", p.control(path, "audit-kafka"))
            storage(path)
        return
    if mode in ("publication-failure", "compaction-failure"):
        from recovery import publication, compaction

        with lab.Observer(path):
            (publication if mode == "publication-failure" else compaction)(
                path, fixture
            )
            storage(path)
        return
    if mode == "down":
        with lab.Observer(path):
            final = p.verify(
                path,
                fixture["phases"],
                frozen=p.settled(path, fixture["phases"], require_running=False),
                replicas=True,
            )
            lab.save(
                path / "final-verification.json",
                final,
            )
            active = p.job()
            if active:
                # Source is already finished; drain then cancel, not a producer pause during chaos.
                lab.compose(
                    "exec",
                    "-T",
                    "jobmanager",
                    "/opt/flink/bin/flink",
                    "stop",
                    "--drain",
                    "--savepointPath",
                    "file:///state/savepoints",
                    active["jid"],
                    timeout=180,
                )
            uri = p.metadata(path)
            drained = p.summary(path, uri)
            lab.save(path / "drained-snapshot.json", drained)
            if drained["snapshot"] != final["snapshot"]["snapshot"]:
                lab.save(
                    path / "post-drain-verification.json",
                    p.verify(path, fixture["phases"], frozen=drained, replicas=True),
                )
            inventory = path / "object-inventory.json"
            lab.save(inventory, p.control(path, "inventory"))
            retention = lab.actor(
                path,
                "lake-oracle",
                ["retention", uri, "--inventory", "/app/inventory.json"],
                extra=("--volume", str(inventory) + ":/app/inventory.json:ro"),
                timeout=240,
            )
            lab.save(
                path / "retention.json",
                next(r for r in lab.records(retention) if "retained_snapshots" in r),
            )
            export_candidates(path, uri, path / "retention.json")
            # Export after the writer is fenced, before stopping read services needed for a safe retry.
            preserve(path)
            lab.compose(
                "stop",
                "--timeout",
                "30",
                "taskmanager1",
                "taskmanager2",
                "spark-worker1",
                "spark-worker2",
                "spark-master",
                "jobmanager",
                timeout=180,
            )
            storage(path)
            lab.snapshot(path, "pre-stop")
            lab.compose("stop", "--timeout", "30", timeout=180)
        (path / "stopped-inspect.json").write_text(
            lab.output(
                "docker", "inspect", *lab.compose("ps", "--all", "--quiet").split()
            )
        )
        fixture["exported_to"] = str(path)
        lab.save(STATE, fixture)
        return
    if mode == "docker-down":
        if not fixture.get("exported_to"):
            raise ValueError("Run make down to reconcile/export histories first")
        lab.compose(
            "--profile",
            "tools",
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "30",
            timeout=180,
        )
        remnants = {}
        for resource in ("container", "network", "volume"):
            ids = lab.output(
                "docker",
                resource,
                "ls",
                "--filter",
                "label=com.docker.compose.project=" + lab.PROJECT,
                "--quiet",
            ).split()
            if ids:
                remnants[resource] = ids
        lab.save(path / "remnants.json", remnants)
        if remnants:
            raise ValueError("Owned resources remain")
        lab.save(path / "retired-fixture.json", fixture)
        STATE.unlink()
        return
    raise ValueError("Unknown lifecycle action")


if __name__ == "__main__":
    (lab.ROOT / "results").mkdir(exist_ok=True)
    with (lab.ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        phase = lab.phase(sys.argv[1])
        try:
            main(sys.argv[1], phase)
            save_status(phase, passed=True)
        except BaseException as error:
            save_status(phase, passed=False, error=str(error))
            try:
                (phase / "diagnostic-compose.log").write_text(
                    lab.compose("logs", "--no-color", "--tail", "150", timeout=45)
                )
            except subprocess.SubprocessError:
                pass
            raise
