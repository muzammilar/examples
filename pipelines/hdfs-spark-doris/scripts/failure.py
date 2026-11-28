"""Prove an active Spark task, hold one owned role down, then verify recovery."""

import json
import subprocess
import time
import lab


def rest(path):
    script = (
        "import urllib.request; print(urllib.request.urlopen("
        + repr("http://orders:4040/api/v1" + path)
        + ",timeout=2).read().decode())"
    )
    try:
        return json.loads(
            lab.compose("exec", "-T", "master", "python3", "-c", script, timeout=10)
        )
    except (subprocess.SubprocessError, ValueError):
        return None


def exercise(path, mode):
    batch = "executor" if mode == "executor-failure" else "datanode"
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
            "/lab/published/" + batch,
        ],
        capture_output=True,
    )
    if exists.returncode == 0:
        raise ValueError(
            "Fault batch already exists; refusing a false replay qualification"
        )
    name = lab.PROJECT + "-fault-driver-" + str(time.time_ns())
    victim = None
    native_proof = None
    started = time.monotonic()
    with (path / "driver.log").open("x") as log:
        child = subprocess.Popen(
            [
                *lab.COMPOSE,
                "run",
                "--name",
                name,
                "--use-aliases",
                "orders",
                "join",
                batch,
            ],
            cwd=lab.ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 150
            while child.poll() is None and time.monotonic() < deadline:
                apps = rest("/applications")
                if apps:
                    jobs = rest(
                        "/applications/" + apps[0]["id"] + "/jobs?status=running"
                    )
                    writing = [
                        j
                        for j in jobs or []
                        if j.get("jobGroup") == batch + "-publication-write"
                    ]
                    executors = rest("/applications/" + apps[0]["id"] + "/executors")
                    active = [
                        e
                        for e in executors or []
                        if e["id"] != "driver" and e.get("activeTasks", 0) > 0
                    ]
                    if active and writing:
                        native_proof = dict(
                            app=apps[0]["id"],
                            active_executors=active,
                            publication_jobs=writing,
                            observed_ns=time.time_ns(),
                        )
                        role = (
                            active[0]["hostPort"].split(":")[0]
                            if mode == "executor-failure"
                            else "dn1"
                        )
                        if role not in ("worker1", "worker2", "dn1"):
                            # Native executors can report their container IP instead of configured DNS.
                            candidates = {}
                            for worker in ("worker1", "worker2"):
                                identifier = lab.compose(
                                    "ps", "--quiet", worker
                                ).strip()
                                row = lab.owned(identifier, worker)
                                for network in row["NetworkSettings"][
                                    "Networks"
                                ].values():
                                    candidates[network["IPAddress"]] = worker
                            role = candidates.get(role)
                        if role not in ("worker1", "worker2", "dn1"):
                            raise ValueError(
                                "Cannot map active executor to owned worker"
                            )
                        victim = role
                        identifier = lab.compose("ps", "--quiet", victim).strip()
                        before = lab.owned(identifier, victim)
                        lab.save(
                            path / "victim-before-kill-cgroups.json",
                            lab.counters(identifier),
                        )
                        killed = time.monotonic()
                        lab.compose("kill", "--signal", "SIGKILL", victim)
                        dead = lab.owned(identifier, victim)
                        if dead["State"]["Running"] or dead["State"]["ExitCode"] != 137:
                            raise ValueError("SIGKILL not proven")
                        lab.save(
                            path / "fault.json",
                            dict(
                                role=victim,
                                before=before["State"],
                                killed=dead["State"],
                                task_proof=native_proof,
                                held_s=15,
                            ),
                        )
                        # A fresh independent reader checks the acknowledged older batch
                        # while Spark keeps writing and the fault remains in place.
                        lab.actor(path, "parquet-verifier", ["baseline"], timeout=60)
                        time.sleep(max(0, 15 - (time.monotonic() - killed)))
                        (path / "degraded-hdfs-report.log").write_text(
                            lab.hdfs("dfsadmin", "-report")
                        )
                        if (
                            victim == "dn1"
                            and "Live datanodes (2)"
                            not in (path / "degraded-hdfs-report.log").read_text()
                        ):
                            raise ValueError(
                                "NameNode did not observe the sustained DataNode outage"
                            )
                        lab.compose("start", victim)
                        lab.save(
                            path / "restored.json",
                            dict(
                                role=victim,
                                fault_to_restart_command_s=time.monotonic() - killed,
                            ),
                        )
                        break
                time.sleep(0.5)
            if not victim:
                raise RuntimeError("No active task observed; no fault injected")
            code = child.wait(timeout=600)
            lab.save(path / "driver-inspect.json", lab.owned(name, "orders"))
            if code:
                raise RuntimeError("Faulted job failed; preserve its unpublished stage")
            log_text = (path / "driver.log").read_text()
            if "PIPELINE_RESULT " not in log_text:
                raise RuntimeError("Missing verified publication result")
            # Retain and inspect the native event log, not just the driver's exit code.
            events = lab.hdfs("dfs", "-cat", "/lab/events/*")
            (path / "spark-events.jsonl").write_text(events)
            records = [
                json.loads(line) for line in events.splitlines() if line.startswith("{")
            ]
            current_app = None
            this_job = []
            for event in records:
                if event.get("Event") == "SparkListenerApplicationStart":
                    current_app = event.get("App ID")
                if current_app == native_proof["app"]:
                    this_job.append(event)
            failed_tasks = [
                r
                for r in this_job
                if r.get("Event") == "SparkListenerTaskEnd"
                and r.get("Task End Reason", {}).get("Reason") != "Success"
            ]
            if mode == "executor-failure" and not failed_tasks:
                raise RuntimeError("No native failed-task evidence")
            lab.save(
                path / "recovery.json",
                dict(
                    passed=True,
                    batch=batch,
                    wall_s=time.monotonic() - started,
                    failed_tasks=failed_tasks,
                    publication_verified=True,
                ),
            )
            lab.output("docker", "rm", name)
            return batch
        finally:
            if victim:
                lab.compose("start", victim)
            if child.poll() is None:
                # Stop only this example's driver; don't leave an unobserved writer.
                lab.owned(name, "orders")
                lab.output("docker", "stop", "--time", "20", name)
                child.wait(timeout=45)
