"""Five lifecycle commands; failures preserve state and acknowledged histories."""

import json
import subprocess
import sys
import uuid

import lab


def preflight():
    endpoint = json.loads(
        subprocess.check_output(["docker", "context", "inspect"], text=True)
    )[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise RuntimeError("A local Unix Docker engine is required")
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    config = json.loads(lab.command("--profile", "tools", "config", "--format", "json"))
    services = config["services"]
    cpu = sum(
        float(row.get("cpus", 0)) * row.get("deploy", {}).get("replicas", 1)
        for row in services.values()
    )
    memory = sum(
        int(row.get("mem_limit", 0)) * row.get("deploy", {}).get("replicas", 1)
        for row in services.values()
    )
    if cpu > info["NCPU"] or memory + 2 * 1024**3 > info["MemTotal"]:
        raise RuntimeError("Combined caps plus 2 GiB headroom exceed Docker resources")
    active = subprocess.check_output(
        ["docker", "ps", "--format", "{{.ID}}"], text=True
    ).split()
    for identifier in active:
        lab.assert_owned(identifier)
    disk = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cpus",
            ".1",
            "--memory",
            "64m",
            "--entrypoint",
            "df",
            "ubuntu:24.04@sha256:562111b88caa79c71391c0c4d3c2114a7d5c35915c20561fd893cb1e07929e6b",
            "--output=avail",
            "-B1",
            "/",
        ],
        text=True,
    )
    if int(disk.splitlines()[-1].strip()) < 8 * 1024**3:
        raise RuntimeError("Need 8 GiB free Docker disk before starting")
    return {
        "environment": {
            key: info.get(key)
            for key in (
                "NCPU",
                "MemTotal",
                "Architecture",
                "KernelVersion",
                "ServerVersion",
                "OperatingSystem",
            )
        },
        "declared_all_roles_cpu": cpu,
        "declared_all_roles_memory_bytes": memory,
        "headroom_bytes": 2 * 1024**3,
    }


def main():
    mode = sys.argv[1]
    path = lab.directory(mode)
    if mode == "docker-up":
        lab.save(path / "environment.json", preflight())
        (path / "build.log").write_text(
            lab.command("build", "fe1", "be1", "jobmanager", "orders", timeout=2400)
        )
        (path / "startup.log").write_text(
            lab.command("up", "-d", "--wait", *lab.ROLES, timeout=900)
        )
    elif mode in ("up", "resume-up"):
        index = lab.ROOT / "results/fixture.json"
        if index.exists():
            if mode != "resume-up":
                raise RuntimeError(
                    "Retained fixture exists; use verify, or resume-up only for initializing setup"
                )
            value = lab.fixture()
            if value["stage"] != "initializing":
                raise RuntimeError("Only an initializing fixture may resume setup")
        else:
            if mode == "resume-up":
                raise RuntimeError("No initializing fixture to resume")
            value = {"id": uuid.uuid4().hex, "stage": "initializing"}
            lab.save(index, value)
        lab.workload("setup", path)
        for topic, partitions in (
            ("cdc.public.orders", 4),
            ("cdc.public.customers", 1),
            ("__debezium-heartbeat.cdc", 1),
        ):
            lab.kafka(
                "--create",
                "--if-not-exists",
                "--topic",
                topic,
                "--partitions",
                partitions,
                "--replication-factor",
                3,
                "--config",
                "min.insync.replicas=2",
            )
        connector = json.loads((lab.ROOT / "config/debezium.json").read_text())
        lab.save(
            path / "registration.json",
            lab.api(
                "/connectors/orders/config", connect=True, method="PUT", data=connector
            ),
        )

        def connector_ready():
            status = lab.api("/connectors/orders/status", connect=True, missing_ok=True)
            if status is None:
                return False
            if status["connector"]["state"] == "FAILED" or any(
                task["state"] == "FAILED" for task in status["tasks"]
            ):
                raise RuntimeError(json.dumps(status))
            return (
                status["connector"]["state"] == "RUNNING"
                and len(status["tasks"]) == 1
                and status["tasks"][0]["state"] == "RUNNING"
            )

        lab.wait_for(connector_ready)
        lab.wait_for(lambda: len(lab.api("/taskmanagers")["taskmanagers"]) == 2)
        lab.command(
            "exec",
            "-T",
            "--user",
            "root",
            "jobmanager",
            "sh",
            "-c",
            "mkdir -p /data/checkpoints; chown -R flink:flink /data",
        )
        submission = lab.command("run", "--rm", "sql", timeout=240)
        (path / "submission.log").write_text(submission)
        if "[ERROR]" in submission:
            raise RuntimeError(
                "Native SQL client reported an error despite its process exit status; see preserved submission.log"
            )
        jobs = lab.wait_for(
            lambda: [
                job
                for job in lab.api("/jobs/overview")["jobs"]
                if job["state"] == "RUNNING"
            ]
        )
        if len(jobs) != 1:
            raise RuntimeError("Expected one native streaming join job")
        value["job"] = jobs[0]["jid"]
        lab.save(index, value)
        lab.wait_for(
            lambda: lab.api("/jobs/" + value["job"] + "/checkpoints")["counts"][
                "completed"
            ]
            >= 1
            and lab.api("/jobs/" + value["job"])["state"] == "RUNNING",
            timeout=180,
        )
        value["stage"] = "ready"
        lab.save(index, value)
        lab.native(path)
    elif mode == "test":
        lab.workload("seed", path)
        lab.workload("verify", path)
        lab.workload("dimensions", path)
        lab.workload("verify", path)
        lab.native(path)
    elif mode == "dimension-test":
        lab.workload("dimensions", path)
        lab.workload("verify", path)
        lab.native(path)
    elif mode == "delete-test":
        lab.workload("delete", path)
        lab.workload("verify", path)
        lab.native(path)
    elif mode == "verify-replicas":
        value = lab.fixture()
        for replica in range(3):
            output = lab.command(
                "exec",
                "-T",
                "-e",
                "RUN_ID=" + value["id"],
                "-e",
                "REPLICA_INDEX=" + str(replica),
                "orders",
                "/orders",
                "verify",
                timeout=1000,
            )
            (path / ("replica-" + str(replica) + ".log")).write_text(output)
        lab.native(path)
    elif mode == "verify":
        lab.workload("verify", path)
        lab.native(path)
    elif mode == "down":
        lab.workload("verify", path)
        lab.native(path)
        job = lab.fixture()["job"]
        lab.api("/jobs/" + job, method="PATCH")
        lab.wait_for(lambda: lab.api("/jobs/" + job)["state"] == "CANCELED")
        lab.api("/connectors/orders", connect=True, method="DELETE")
        lab.workload("drop", path)
        lab.save(
            lab.ROOT / "results" / ("down-" + lab.fixture()["id"] + ".json"),
            {
                "fixture": lab.fixture()["id"],
                "verified": True,
                "evidence": str(path.relative_to(lab.ROOT)),
            },
        )
    elif mode == "docker-down":
        fixture = lab.fixture()
        receipt = json.loads(
            (lab.ROOT / "results" / ("down-" + fixture["id"] + ".json")).read_text()
        )
        if receipt["fixture"] != fixture["id"]:
            raise RuntimeError("Teardown receipt belongs to another fixture")
        (path / "removed.log").write_text(
            lab.command(
                "--profile",
                "tools",
                "down",
                "--volumes",
                "--remove-orphans",
                "--rmi",
                "local",
            )
        )
        for kind in ("container", "volume", "network"):
            args = (
                ["docker", "ps", "--all", "--quiet"]
                if kind == "container"
                else ["docker", kind, "ls", "--quiet"]
            )
            if subprocess.check_output(
                [*args, "--filter", "label=com.docker.compose.project=" + lab.PROJECT],
                text=True,
            ).strip():
                raise RuntimeError("Owned resources remain: " + kind)
        index = lab.ROOT / "results/fixture.json"
        index.rename(index.with_name("retired-fixture-" + fixture["id"] + ".json"))
        lab.save(
            path / "cleanup.json",
            {
                "fixture": fixture["id"],
                "containers_absent": True,
                "volumes_absent": True,
                "network_absent": True,
            },
        )
    else:
        raise ValueError(mode)
    lab.save(path / "status.json", {"passed": True, "mode": mode})
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
