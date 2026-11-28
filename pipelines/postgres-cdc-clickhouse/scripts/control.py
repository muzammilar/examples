"""Make entry points for the isolated snapshot/WAL recovery example."""

import json
import subprocess
import sys
import time
import uuid
from lab import (
    ROOT,
    PROJECT,
    ROLES,
    app,
    api,
    command,
    fixture,
    pg,
    save,
    sql,
    topics,
    wait,
)
from correctness import offset_fence, reconcile
from observations import resources
from recovery import test


def main():
    mode = sys.argv[1]
    path = ROOT / "results" / (mode + "-" + str(time.time_ns()))
    path.mkdir(parents=True)
    if mode == "docker-up":
        if (ROOT / "results/fixture.json").exists():
            raise RuntimeError("Fixture exists; recover/verify rather than reseed")
        config = json.loads(command("config", "--format", "json").stdout)
        cpu = sum(float(s.get("cpus", 0)) for s in config["services"].values())
        memory = sum(int(s.get("mem_limit", 0)) for s in config["services"].values())
        info = json.loads(
            subprocess.check_output(
                ["docker", "info", "--format", "{{json .}}"], text=True
            )
        )
        if cpu > info["NCPU"] or memory + 2 * 1024**3 > info["MemTotal"]:
            raise RuntimeError("Combined lab budget exceeds host reserve")
        save(
            path / "budget.json",
            {
                "cpu": cpu,
                "memory": memory,
                "reserve": 2 * 1024**3,
                "architecture": info["Architecture"],
            },
        )
        command("pull", *ROLES, timeout=900)
        image = config["services"]["postgres"]["image"]
        space = subprocess.check_output(
            [
                "docker",
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
                "32m",
                "--entrypoint",
                "sh",
                image,
                "-c",
                "df -PB1 /",
            ],
            text=True,
        )
        free = int(space.splitlines()[-1].split()[3])
        if free < 12 * 1024**3:
            raise RuntimeError(
                "Less than 12 GiB free after pulling images; refusing startup"
            )
        save(
            path / "disk-preflight.json",
            {"free_bytes": free, "required_free_bytes": 12 * 1024**3},
        )
        command(
            "up", "-d", "--wait", *[r for r in ROLES if r != "connect"], timeout=900
        )
        topics()
        command("up", "-d", "--wait", "connect", "orders-workload", timeout=300)

        def keeper_ready():
            sql("SELECT name FROM system.zookeeper WHERE path='/'")
            return True

        wait(keeper_ready, 120)
        save(
            ROOT / "results/fixture.json",
            {"id": uuid.uuid4().hex, "stage": "infrastructure"},
        )
    elif mode == "up":
        value = fixture()
        if value["stage"] != "infrastructure":
            raise RuntimeError("Application already initialized; verify it")
        app("seed", path)
        for host in ["ch1", "ch2", "ch3"]:
            for name in ["state.sql", "ingest.sql"]:
                command(
                    "exec",
                    "-T",
                    host,
                    "clickhouse-client",
                    "--user",
                    "lab",
                    "--password",
                    "local-control-only",
                    "--multiquery",
                    data=(ROOT / "sql" / name).read_text(),
                )
        value["stage"] = "ready"
        save(ROOT / "results/fixture.json", value)
    elif mode == "test":
        test(path)
    elif mode == "verify":
        offset_fence(path)
        reconcile(path)
        resources(path)
    elif mode == "down":
        offset_fence(path)
        reconcile(path)
        resources(path)
        api("/connectors/handoff", "DELETE")
        wait(
            lambda: pg(
                "SELECT count(*) FROM pg_replication_slots WHERE slot_name='handoff' AND active"
            )
            == "0",
            60,
        )
        for host in ["ch1", "ch2", "ch3"]:
            sql("DROP DATABASE pipeline SYNC", host)
        pg(
            "DROP PUBLICATION pipeline; SELECT pg_drop_replication_slot('handoff'); DROP TABLE receipts,orders,customers;"
        )
        save(
            ROOT / "results" / ("down-" + fixture()["id"] + ".json"),
            {"verified": True, "evidence": str(path.relative_to(ROOT))},
        )
    elif mode == "docker-down":
        value = fixture()
        proof = json.loads(
            (ROOT / "results" / ("down-" + value["id"] + ".json")).read_text()
        )
        if not proof["verified"]:
            raise RuntimeError("No verified cleanup receipt")
        command("down", "--volumes", "--remove-orphans", "--rmi", "local", timeout=120)
        for kind in ["container", "volume", "network"]:
            listing = subprocess.check_output(
                [
                    "docker",
                    kind,
                    "ls",
                    "--quiet",
                    "--filter",
                    "label=com.docker.compose.project=" + PROJECT,
                ],
                text=True,
            )
            if listing.strip():
                raise RuntimeError("Owned " + kind + " remains")
        (ROOT / "results/fixture.json").rename(
            ROOT / "results" / ("retired-fixture-" + value["id"] + ".json")
        )
        save(
            path / "status.json",
            {"passed": True, "fixture": value["id"], "owned_resources_absent": True},
        )
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
