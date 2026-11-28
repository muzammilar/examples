"""Five lifecycle commands plus separately invoked, owned failure cases."""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import lab
from topology import published_replication_restored


def wait(probe, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (subprocess.SubprocessError, ValueError, RuntimeError) as error:
            last = str(error)
        time.sleep(2)
    raise RuntimeError("Readiness timed out: " + str(last))


def oracle(path, mode, batch="baseline", replica=None):
    extra = ("-e", "REPLICA_INDEX=" + str(replica)) if replica is not None else ()
    return lab.actor(path, "oracle", [mode, batch], extra=extra)


def native(path):
    data = lab.hdfs("fsck", "/lab", "-files", "-blocks", "-locations")
    (path / "fsck.log").write_text(data)
    if "Status: HEALTHY" not in data:
        raise ValueError("HDFS not healthy/full replication; inspect fsck")
    (path / "hdfs-report.log").write_text(lab.hdfs("dfsadmin", "-report"))
    from topology import check_doris, hdfs_replicas

    topology = check_doris(oracle(path, "topology"))
    topology["hdfs"] = hdfs_replicas(path)
    lab.save(path / "topology-verdict.json", topology)


def load_verify(path, batch):
    lab.actor(path, "parquet-verifier", [batch])
    oracle(path, "load", batch)
    for replica in range(3):
        oracle(path, "verify", batch, replica)
    before = oracle(path, "topology")
    oracle(
        path, "load", batch
    )  # Reconcile existing FINISHED labels, never blind replay.
    after = oracle(path, "topology")

    def receipts(file):
        return [
            row
            for row in lab.records(file)
            if row.get("query") == "SHOW LOAD FROM pipeline"
        ][0]["rows"]

    if receipts(before) != receipts(after):
        raise ValueError("Repeated publication changed load receipts")
    oracle(path, "verify", batch)


def verify_all(path):
    found = []
    for batch in ("baseline", "executor", "datanode"):
        probe = subprocess.run(
            [
                *lab.COMPOSE,
                "exec",
                "-T",
                "namenode",
                "hdfs",
                "dfs",
                "-test",
                "-e",
                "/lab/published/" + batch,
            ],
            capture_output=True,
        )
        if probe.returncode == 0:
            load_verify(path, batch)
            found.append(batch)
        elif probe.returncode != 1:
            raise RuntimeError("HDFS existence probe failed")
    if "baseline" not in found:
        raise ValueError("Missing acknowledged baseline")
    lab.save(path / "verified-batches.json", found)


def storage(path):
    (path / "hdfs-space.log").write_text(lab.hdfs("dfs", "-du", "-s", "/lab"))
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
            "type=volume,source=" + name + ",target=/storage/" + name + ",readonly",
        ]
    result = lab.output(
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
        "python3",
        "examples/hdfs-spark-doris-hadoop:3.5.0",
        "/app/volume_usage.py",
    )
    (path / "volumes.json").write_text(result)


def main():
    mode = sys.argv[1]
    path = lab.phase(mode)
    fixture_path = lab.ROOT / "results/fixture.json"
    if mode == "docker-up":
        previous = (
            json.loads(fixture_path.read_text()) if fixture_path.exists() else None
        )
        if previous is None and lab.compose("ps", "--all", "--quiet").strip():
            raise ValueError("Owned containers already exist without fixture")
        config = json.loads(
            lab.compose("--profile", "tools", "config", "--format", "json")
        )
        info = json.loads(lab.output("docker", "info", "--format", "{{json .}}"))
        cpus = sum(float(x.get("cpus", 0)) for x in config["services"].values())
        memory = sum(int(x.get("mem_limit", 0)) for x in config["services"].values())
        if cpus > info["NCPU"] or memory + 2 * 1024**3 > info["MemTotal"]:
            raise ValueError("Combined roles/tools + 2GiB exceed Docker capacity")
        save = dict(
            id=uuid.uuid4().hex,
            rows=int(os.environ.get("ORDER_COUNT", "1000000")),
            customers=int(os.environ.get("CUSTOMER_COUNT", "10000")),
            cpu_caps=cpus,
            memory_caps=memory,
            docker_cpus=info["NCPU"],
            docker_memory=info["MemTotal"],
            kernel=info["KernelVersion"],
            driver="spark-native/parquet;mysql-go1.11.0",
            local_only=True,
        )
        if previous:
            if any(
                previous[key] != save[key]
                for key in ("rows", "customers", "cpu_caps", "memory_caps")
            ):
                raise ValueError(
                    "Existing fixture/configuration differs; refusing replacement"
                )
            save = previous
        lab.save(fixture_path, save)
        lab.save(path / "fixture.json", save)
        with (path / "build.log").open("w") as log:
            subprocess.run(
                [*lab.COMPOSE, "--profile", "tools", "build"],
                cwd=lab.ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=3600,
            )
        with lab.Observer(path):
            lab.compose("up", "--detach", *lab.ROLES)
            wait(lambda: "Live datanodes (3)" in lab.hdfs("dfsadmin", "-report"), 240)
            wait(
                lambda: lab.compose(
                    "exec", "-T", "master", "python3", "/app/wait_workers.py"
                )
            )
            wait(lambda: oracle(path, "setup"), 240)
            lab.hdfs("dfs", "-mkdir", "-p", "/lab/events")
            lab.hdfs("dfs", "-chmod", "-R", "777", "/lab")
            from topology import check_doris

            wait(lambda: check_doris(oracle(path, "topology")), 180)
            storage(path)
    else:
        fixture = json.loads(fixture_path.read_text())
        if fixture["rows"] != int(os.environ.get("ORDER_COUNT", "1000000")) or fixture[
            "customers"
        ] != int(os.environ.get("CUSTOMER_COUNT", "10000")):
            raise ValueError("Workload overrides differ from preserved fixture")
        with lab.Observer(path):
            if mode == "up":
                exists = subprocess.run(
                    [
                        *lab.COMPOSE,
                        "exec",
                        "-T",
                        "namenode",
                        "hdfs",
                        "dfs",
                        "-test",
                        "-e",
                        "/lab/input",
                    ],
                    capture_output=True,
                )
                if exists.returncode:
                    lab.actor(path, "orders", ["seed", "baseline"])
                lab.actor(path, "orders", ["join", "baseline"])
                load_verify(path, "baseline")
                storage(path)
            elif mode in ("test", "verify"):
                lab.actor(path, "orders", ["verify", "baseline"])
                verify_all(path)
                native(path)
                storage(path)
            elif mode in ("executor-failure", "datanode-failure"):
                from failure import exercise

                batch = exercise(path, mode)
                load_verify(path, batch)
                wait(
                    lambda: published_replication_restored(path),
                    180,
                )
                native(path)
                storage(path)
            elif mode == "observe-repair":
                from repair import observe

                observe(path)
                native(path)
                storage(path)
            elif mode == "benchmark":
                oracle(path, "verify")
                oracle(path, "query")
                storage(path)
            elif mode == "resources":
                native(path)
                storage(path)
            elif mode == "down":
                verify_all(path)
                lab.snapshot(path, "pre-stop")
                lab.compose("stop", *lab.ROLES)
            elif mode == "docker-down":
                lab.compose("down", "--volumes", "--remove-orphans")
                if (
                    lab.compose("ps", "--all", "--quiet").strip()
                    or lab.output(
                        "docker",
                        "volume",
                        "ls",
                        "--filter",
                        "label=com.docker.compose.project=" + lab.PROJECT,
                        "--quiet",
                    ).strip()
                ):
                    raise ValueError("Owned state remains")
                fixture_path.rename(path / "destroyed-fixture.json")
            else:
                raise ValueError("Unknown command")
    started = json.loads((path / "status.json").read_text())["started_ns"]
    lab.save(
        path / "status.json",
        {
            "passed": True,
            "mode": mode,
            "started_ns": started,
            "ended_ns": time.time_ns(),
        },
    )
    print(path)


if __name__ == "__main__":
    (lab.ROOT / "results").mkdir(exist_ok=True)
    with (lab.ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
