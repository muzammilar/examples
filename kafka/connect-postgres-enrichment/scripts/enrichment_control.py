"""Serial, witness-backed enrichment experiments in this Compose project."""

import fcntl
import hashlib
import json
import os
import re
import resource
import signal
import sys
import time
import uuid

import enrichment_lab as lab
import enrichment_metrics as metrics
import enrichment_probes as probes
import order_report


def partitions(text):
    rows = []
    for line in text.splitlines():
        fields = dict(
            re.findall(r"(Topic|Partition|Leader|Replicas|Isr):\s*(\S+)", line)
        )
        if "Partition" in fields:
            rows.append(
                {
                    "topic": fields["Topic"],
                    "partition": int(fields["Partition"]),
                    "leader": int(fields["Leader"]),
                    "replicas": set(map(int, fields["Replicas"].split(","))),
                    "isr": set(map(int, fields.get("Isr", "").split(","))),
                }
            )
    if not rows:
        raise RuntimeError("native partition metadata missing")
    return rows


def native():
    states = {n: lab.service(n) for n in lab.BASE}
    if any(not s["State"]["Running"] for s in states.values()):
        return None
    rows = partitions(lab.cli("kafka-topics.sh", "--describe"))
    if any(
        r["replicas"] != {4, 5, 6}
        or r["isr"] != {4, 5, 6}
        or r["leader"] not in {4, 5, 6}
        for r in rows
    ):
        return None
    quorum = lab.cli("kafka-metadata-quorum.sh", "describe", "--status")
    voters = re.search(r"^CurrentVoters:\s*(.*)$", quorum, re.M)
    if (
        not voters
        or set(map(int, re.findall(r'"id"\s*:\s*(\d+)', voters[1]))) != {1, 2, 3}
        or not re.search(r"^MaxFollowerLag:\s*0$", quorum, re.M)
    ):
        return None
    text = lab.cli("kafka-log-dirs.sh", "--describe", "--broker-list", "4,5,6")
    logs = next(
        (json.loads(line) for line in text.splitlines() if line.startswith("{")), None
    )
    if logs is None or {b["broker"] for b in logs["brokers"]} != {4, 5, 6}:
        return None
    if any(
        d["error"] is not None or any(p["offsetLag"] != 0 for p in d["partitions"])
        for b in logs["brokers"]
        for d in b["logDirs"]
    ):
        return None
    connector = lab.connect_status()
    if (
        connector["connector"]["state"] != "RUNNING"
        or len(connector["tasks"]) != 1
        or connector["tasks"][0]["state"] != "RUNNING"
    ):
        return None
    pg = json.loads(
        lab.sql(
            "SELECT json_build_object('fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'customers',(SELECT count(*) FROM customers),'probe',(SELECT cdc_probe FROM customers WHERE id='customer-0'),'slot_active',(SELECT active FROM pg_replication_slots WHERE slot_name='customers_cdc'))"
        )
    )
    status = lab.wait(
        lambda: (s if s["cdc_probe"] >= pg["probe"] else None)
        if (s := lab.processor_status())
        else None,
        "captured native CDC barrier",
        10,
    )
    if (
        pg["fsync"] != "on"
        or pg["synchronous_commit"] != "on"
        or pg["customers"] != 10000
        or not pg["slot_active"]
        or not status["ready"]
        or status["customers"] != 10000
        or status["cdc_probe"] < pg["probe"]
    ):
        return None
    return {
        "time": time.time(),
        "identities": {
            n: {"id": s["Id"], "start": s["State"]["StartedAt"], "mounts": s["Mounts"]}
            for n, s in states.items()
        },
        "partitions": [
            dict(r, replicas=sorted(r["replicas"]), isr=sorted(r["isr"])) for r in rows
        ],
        "quorum": quorum,
        "logs": logs,
        "connector": connector,
        "postgres": pg,
        "processor": status,
    }


def check_negative():
    path = lab.STATE / "cdc-reproducer/fingerprints.json"
    if path.exists():
        expected = json.loads(path.read_text())
        actual = {topic: lab.fingerprint(topic) for topic in expected}
        if json.loads(json.dumps(actual)) != expected:
            raise RuntimeError("known failed CDC fixture changed")


def setup_only_failure(root, *, seal=False):
    receipt_path = root / "setup-only-reconciliation.json"
    if not seal and not receipt_path.exists():
        return False
    metadata = json.loads((root / "experiment.json").read_text())
    if metadata["owner"]["owner"] != lab.owner()["owner"]:
        raise RuntimeError("setup failure belongs to a different lab")
    for side in ("source", "enriched"):
        if any(
            (root / side / name).exists()
            for name in ("producer.jsonl.gz", "reader.jsonl.gz")
        ):
            raise RuntimeError(
                "cannot seal a setup failure after workload journals started"
            )
    offsets = {}
    for topic in (metadata["input"], metadata["output"]):
        text = lab.cli("kafka-get-offsets.sh", "--topic", topic, "--time", "-1")
        rows = {
            int(p): int(n)
            for _, p, n in (line.rsplit(":", 2) for line in text.splitlines())
        }
        if set(rows) != set(range(6)) or any(rows.values()):
            raise RuntimeError("failed setup has Kafka data; full diagnosis required")
        offsets[topic] = rows
    digest = hashlib.sha256((root / "failure.json").read_bytes()).hexdigest()
    if seal:
        report = probes.reconcile(root, json.loads((root / "phases.json").read_text()))
        lab.save(
            receipt_path,
            {
                "original_status": "failed",
                "owner": lab.owner()["owner"],
                "failure_sha256": digest,
                "zero_end_offsets": offsets,
                "postgres_reconciliation": report,
            },
        )
    else:
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt["failure_sha256"] != digest
            or receipt["owner"] != lab.owner()["owner"]
        ):
            raise RuntimeError("sealed setup failure changed")
    return True


def reconciled_failure(root):
    """A separate read-only receipt releases the gate, never the failed verdict."""
    path = root / "post-interruption-reconciliation.json"
    if not path.exists():
        return False
    receipt = json.loads(path.read_text())
    for name in ("failure.json", "experiment.json"):
        if (
            hashlib.sha256((root / name).read_bytes()).hexdigest()
            != receipt["sha256"][name]
        ):
            raise RuntimeError("original interrupted case changed")
    for side in ("source", "enriched"):
        proof = receipt["proofs"][side]
        report = lab.STATE / proof["path"] / "verification.json"
        if not report.resolve().is_relative_to(
            (root / side / "verifications").resolve()
        ):
            raise RuntimeError("verification is outside the interrupted case")
        if hashlib.sha256(report.read_bytes()).hexdigest() != proof["sha256"]:
            raise RuntimeError("post-interruption verification changed")
        result = json.loads(report.read_text())
        if (
            not result["strict_verification"]
            or result["acknowledged_loss"]
            or result["corruption"]
            or result["observed_acknowledged"] != result["acknowledged"]
            or result["run_id"] != root.name
            or result["mirrored_offset_policy"] != (side == "enriched")
        ):
            raise RuntimeError("invalid post-interruption verification")
    return True


class Experiment:
    def __init__(self, operation):
        if operation not in {
            "baseline",
            "broker",
            "connect",
            "processor",
            "postgres",
            "benchmark",
        }:
            raise ValueError("unknown experiment")
        self.id = uuid.uuid4().hex
        self.root = lab.STATE / "cases" / self.id
        self.root.mkdir(parents=True)
        self.operation = operation
        self.host_started = time.monotonic()
        self.host_usage = resource.getrusage(resource.RUSAGE_SELF)
        self.child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.phases = []
        self.workloads = []
        self.recorder = None
        self.probes = None
        self.fault = None
        self.count = int(os.environ.get("COUNT", "2000000"))
        if not 1000000 <= self.count <= 2000000:
            raise ValueError("COUNT must be one to two million")
        self.input = "orders-" + str(time.time_ns())
        self.output = self.input + ".enriched"
        self.env = {
            "INPUT_TOPIC": self.input,
            "OUTPUT_TOPIC": self.output,
            "PROCESSOR_GROUP": "enrich-" + self.id,
            "TRANSACTIONAL_ID": "enrich-" + self.id,
        }

    def phase(self, name, **detail):
        marker = {"time": time.time(), "phase": name} | detail
        self.phases.append(marker)
        lab.save(self.root / "phases.json", self.phases, replace=True)
        if self.recorder is not None and name != "failed":
            lab.save(
                self.root / ("storage-" + str(time.time_ns()) + ".json"),
                {"phase": name, "snapshot": metrics.storage_snapshot()},
            )
        print(json.dumps({"case": self.id} | marker), flush=True)

    def progress(self, side, name):
        path = self.root / side / (name + "-progress.json")
        return json.loads(path.read_text()) if path.exists() else {}

    def check(self):
        self.recorder.check()
        self.probes.check()
        for identity in self.workloads:
            state = lab.inspect(identity)["State"]
            if not state["Running"]:
                (self.root / (identity[:12] + ".log")).write_text(lab.logs(identity))
                raise RuntimeError("live workload exited before its recovery gate")

    def observe(self):
        self.check()
        try:
            status = lab.processor_status()
        except Exception as error:
            status = {"error": str(error)}
        event = {
            "time": time.time(),
            "phase": self.phases[-1]["phase"],
            "source": self.progress("source", "reader"),
            "enriched": self.progress("enriched", "reader"),
            "producer": self.progress("source", "producer"),
            "processor": status,
            "postgres_last_ack": self.probes.last_version,
            "postgres_acks": self.probes.acknowledged,
            "postgres_reads": self.probes.reads,
        }
        with (self.root / "observations.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def healthy(self):
        self.check()
        try:
            snapshot = native()
        except Exception as error:
            lab.save(
                self.root / ("native-error-" + str(time.time_ns()) + ".json"),
                {"error": str(error)},
            )
            return None
        if snapshot:
            lab.save(self.root / ("native-" + str(time.time_ns()) + ".json"), snapshot)
        return snapshot

    def verify(self, case, side):
        actor = lab.STATE / "cases" / case / side
        output = actor / "verifications" / uuid.uuid4().hex
        identity = lab.launch("witness-verifier", actor, output=output)
        try:
            lab.wait(
                lambda: lab.completed(identity, output),
                "strict full-history replay",
                900,
            )
        except BaseException:
            try:
                lab.inspect(identity)
                lab.call(["docker", "stop", "--time", "10", identity], timeout=30)
                lab.save(output / "interrupted-container.json", lab.inspect(identity))
            except Exception as cleanup:
                lab.save(output / "cleanup-failed.json", {"error": str(cleanup)})
            raise
        result = json.loads((output / "verification.json").read_text())
        if (
            not result["strict_verification"]
            or result["acknowledged_loss"]
            or result["corruption"]
            or result["observed_acknowledged"] != result["acknowledged"]
            or result["mirrored_offset_policy"] != (side == "enriched")
        ):
            raise RuntimeError("strict full-body/ACK reconciliation failed")
        return {"path": str(output.relative_to(lab.STATE)), "result": result}

    def crash(self, role):
        original = lab.service(role)
        identity = original["Id"]
        policy = original["HostConfig"]["RestartPolicy"]["Name"]
        lab.save(self.root / "fault-before.json", original)
        lab.call(["docker", "update", "--restart=no", identity])
        self.fault = (role, identity, policy)
        before = self.observe()["producer"]["acknowledged"]
        self.phase("fault", role=role, id=identity)
        lab.call(["docker", "kill", "--signal", "KILL", identity])
        stopped = lab.wait(
            lambda: (r if not r["State"]["Running"] else None)
            if (r := lab.inspect(identity))
            else None,
            "positive process fencing",
            30,
        )
        lab.save(self.root / "fault-fenced.json", stopped)
        if stopped["State"]["ExitCode"] != 137 or stopped["State"]["OOMKilled"]:
            raise RuntimeError("unexpected process termination")
        self.phase("fenced")
        end = time.monotonic() + 15
        while time.monotonic() < end:
            self.observe()
            time.sleep(1)
        if self.observe()["producer"]["acknowledged"] <= before:
            raise RuntimeError("no source writes while degraded")
        if role.startswith("connect-"):

            def takeover():
                self.observe()
                status = lab.connect_status()
                tasks = status.get("tasks", [])
                if (
                    len(tasks) == 1
                    and tasks[0]["state"] == "RUNNING"
                    and not tasks[0]["worker_id"].startswith(role + ":")
                ):
                    lab.save(self.root / "connect-takeover.json", status)
                    return True
                return False

            lab.wait(takeover, "source task takeover by surviving Connect worker", 180)
            self.phase("connect-task-takeover")
        lab.call(["docker", "start", identity])
        lab.call(["docker", "update", "--restart=" + policy, identity])
        self.fault = None
        self.phase("restart-requested")

        def restarted():
            self.observe()
            row = lab.inspect(identity)
            return (
                row
                if row["State"]["Running"]
                and row["State"]["StartedAt"] != original["State"]["StartedAt"]
                else None
            )

        after = lab.wait(restarted, "new process incarnation", 120)
        if sorted(after["Mounts"], key=lambda m: m["Destination"]) != sorted(
            original["Mounts"], key=lambda m: m["Destination"]
        ):
            raise RuntimeError("retained-volume crash changed storage")
        lab.save(self.root / "fault-after.json", after)
        self.phase("native-recovery")

    def promote_recovered_broker(self, role):
        broker = int(role.rsplit("-", 1)[1]) + 3
        rows = [
            r
            for r in partitions(lab.cli("kafka-topics.sh", "--describe"))
            if r["topic"] in {self.input, self.output}
        ]
        if len(rows) != 12:
            raise RuntimeError("incomplete placement before repaired-copy promotion")
        plan = {
            "version": 1,
            "partitions": [
                {
                    "topic": r["topic"],
                    "partition": r["partition"],
                    "replicas": [broker] + sorted(r["replicas"] - {broker}),
                }
                for r in rows
            ],
        }
        path = self.root / "repaired-copy-assignment.json"
        lab.save(path, plan)
        target = lab.service(role)
        remote = "/tmp/enrichment-preferred-" + self.id + ".json"
        lab.call(["docker", "cp", str(path), target["Id"] + ":" + remote])
        lab.compose(
            "exec",
            "-T",
            role,
            "/opt/kafka/bin/kafka-reassign-partitions.sh",
            "--bootstrap-server",
            role + ":19092",
            "--reassignment-json-file",
            remote,
            "--execute",
        )
        elections = self.root / "repaired-copy-election.json"
        lab.save(
            elections,
            {
                "partitions": [
                    {"topic": r["topic"], "partition": r["partition"]} for r in rows
                ]
            },
        )
        election_remote = "/tmp/enrichment-election-" + self.id + ".json"
        lab.call(["docker", "cp", str(elections), target["Id"] + ":" + election_remote])
        lab.compose(
            "exec",
            "-T",
            role,
            "/opt/kafka/bin/kafka-leader-election.sh",
            "--bootstrap-server",
            role + ":19092",
            "--election-type",
            "preferred",
            "--path-to-json-file",
            election_remote,
        )

        def serving():
            self.observe()
            rows = [
                r
                for r in partitions(lab.cli("kafka-topics.sh", "--describe"))
                if r["topic"] in {self.input, self.output}
            ]
            return len(rows) == 12 and all(
                r["leader"] == broker and r["isr"] == {4, 5, 6} for r in rows
            )

        lab.wait(serving, "repaired copy leading every input/output partition", 120)
        self.phase("repaired-copy-leading", broker=broker)

    def run(self):
        lab.owner()
        check_negative()
        self.phase("setup")
        self.recorder = metrics.Recorder(self.root)
        self.recorder.start()
        lab.save(self.root / "storage-before.json", metrics.storage_snapshot())
        for topic in (self.input, self.output):
            lab.cli(
                "kafka-topics.sh",
                "--create",
                "--topic",
                topic,
                "--partitions",
                "6",
                "--replication-factor",
                "3",
                "--config",
                "min.insync.replicas=2",
            )
        oracle = lab.customers()
        if len(oracle) != 10000:
            raise RuntimeError("expected exactly 10000 PostgreSQL customers")
        for side, topic in [("source", self.input), ("enriched", self.output)]:
            actor = self.root / side
            actor.mkdir()
            plan = {
                "format_version": 1,
                "run_id": self.id,
                "topic": topic,
                "bootstrap_servers": "kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092",
                "minimum_messages": self.count,
                "warmup_messages": 100000,
                "live_rate": int(os.environ.get("LIVE_RATE", "1000")),
                "burst_rate": 0,
                "duration_seconds": 1800,
                "delivery_timeout_ms": 300000,
                "partitions": 6,
                "group_id": self.id + "-" + side,
                "mirrored": side == "enriched",
                "enriched": side == "enriched",
                "customer_count": 10000,
            }
            lab.save(actor / "case.json", plan)
            lab.save(actor / "customers.json", oracle)
        lab.save(
            self.root / "experiment.json",
            {
                "id": self.id,
                "operation": self.operation,
                "owner": lab.owner(),
                "input": self.input,
                "output": self.output,
                "source_revision": lab.call(["git", "rev-parse", "HEAD"]).strip(),
                "controller_sources_sha256": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in lab.ROOT.glob("*.py")
                },
                "environment": json.loads(
                    lab.call(
                        [
                            "docker",
                            "info",
                            "--format",
                            '{"architecture":"{{.Architecture}}","cpus":{{.NCPU}},'
                            '"memory_bytes":{{.MemTotal}},"kernel":"{{.KernelVersion}}",'
                            '"runtime":"{{.ServerVersion}}"}',
                        ]
                    )
                ),
                "images": {role: lab.service(role)["Image"] for role in lab.BASE},
            },
        )
        lab.compose(
            "up",
            "--no-build",
            "--detach",
            "--wait",
            "processor",
            extra=self.env,
            timeout=180,
        )
        self.probes = probes.Probes(self.root)
        self.probes.start()
        lab.wait(lambda: self.probes.acknowledged > 0, "first witnessed SQL update", 30)
        required_version = self.probes.last_version
        lab.wait(
            lambda: lab.processor_status()["cdc_probe"] >= required_version,
            "captured CDC barrier visible before orders",
            90,
        )
        initial = lab.wait(self.healthy, "initial native replication/CDC health", 120)
        lab.save(self.root / "initial-native.json", initial)
        for side, role in [
            ("source", "source-reader"),
            ("enriched", "enriched-reader"),
        ]:
            self.workloads.append(lab.launch(role, self.root / side))
        if self.operation == "benchmark":
            (self.root / "source/finish-producer").touch()
        producer = lab.launch("witness-producer", self.root / "source")
        self.workloads.append(producer)
        if self.operation != "benchmark":

            def warm():
                e = self.observe()
                return e["producer"].get("acknowledged", 0) >= 100000 and all(
                    e[s].get("unique_messages", 0) >= 100000
                    for s in ["source", "enriched"]
                )

            lab.wait(warm, "independent live writer/readers warmup", 300)
            self.phase("baseline")
            end = time.monotonic() + 10
            while time.monotonic() < end:
                self.observe()
                time.sleep(1)
            if self.operation == "broker":
                rows = partitions(
                    lab.cli("kafka-topics.sh", "--describe", "--topic", self.input)
                )
                leader = next(r["leader"] for r in rows if r["partition"] == 0)
                role = "kafka-broker-" + str(leader - 3)
                self.crash(role)
            elif self.operation == "connect":
                task = lab.connect_status()["tasks"][0]
                role = task["worker_id"].split(":")[0]
                if role not in {"connect-1", "connect-2"}:
                    raise RuntimeError("unknown source task owner")
                self.crash(role)
            elif self.operation in {"processor", "postgres"}:
                self.crash(self.operation)
            lab.wait(self.healthy, "native replication/CDC repair", 300)
            if self.operation == "broker":
                self.phase("native-recovered")
                self.promote_recovered_broker(role)
            self.phase("recovered-live")
            before = self.observe()
            end = time.monotonic() + 15
            while time.monotonic() < end:
                self.observe()
                time.sleep(1)
            after = self.observe()
            if any(
                after[s].get("unique_messages", 0)
                <= before[s].get("unique_messages", 0)
                for s in ["source", "enriched"]
            ):
                raise RuntimeError("readers did not progress after repair")
            self.phase("finish-load")
            (self.root / "source/finish-producer").touch()
        self.workloads.remove(producer)

        def producer_done():
            self.observe()
            return lab.completed(producer, self.root)

        lab.wait(producer_done, "producer completion", 600)
        final = json.loads((self.root / "source/producer.json").read_text())
        ack = final["acknowledged"]
        if (
            ack < self.count
            or final["ambiguous"]
            or final["rejected"]
            or final["aborted"]
        ):
            raise RuntimeError("producer has unresolved outcomes")

        def caught_up():
            e = self.observe()
            return all(
                e[s].get("unique_messages", 0) >= ack for s in ["source", "enriched"]
            )

        lab.wait(caught_up, "all ACKs observed on both paths", 300)
        self.probes.stop()
        self.probes.check()
        lab.wait(self.healthy, "final native repair and CDC catch-up", 300)
        for side in ["source", "enriched"]:
            (self.root / side / "stop-reader").touch()
        for identity in self.workloads:
            lab.wait(
                lambda: lab.completed(identity, self.root), "reader completion", 120
            )
        self.workloads = []
        os.link(
            self.root / "source/producer.jsonl.gz",
            self.root / "enriched/producer.jsonl.gz",
        )
        self.phase("reconcile")
        proofs = {}
        for path in sorted((lab.STATE / "cases").glob("*/experiment.json")):
            case = path.parent
            if case != self.root and setup_only_failure(case):
                proofs[case.name] = {
                    "original_status": "failed",
                    "zero_Kafka_orders": True,
                }
                continue
            if (
                case != self.root
                and (
                    not (case / "result.json").exists()
                    or (case / "failure.json").exists()
                )
                and not reconciled_failure(case)
            ):
                raise RuntimeError("earlier unqualified case must be diagnosed first")
            proofs[case.name] = {
                s: self.verify(case.name, s) for s in ["source", "enriched"]
            }
        reports = {}
        for side in ["source", "enriched"]:
            reports[side] = order_report.reconcile(
                self.root / side,
                json.loads((self.root / side / "case.json").read_text()),
                self.phases,
            )
            reports[side]["wire_bytes"] = sum(
                e["wire_bytes"]
                for e in order_report.events(self.root / side / "reader.jsonl.gz")
                if e["stage"] == "message" and not e["duplicate"]
            )
        sql_report = probes.reconcile(self.root, self.phases)
        check_negative()
        final_native = native()
        if not final_native:
            raise RuntimeError("native redundancy missing after replay")
        target_role = (
            self.operation if self.operation in {"processor", "postgres"} else None
        )
        if (self.root / "fault-before.json").exists():
            target_role = json.loads((self.root / "fault-before.json").read_text())[
                "Config"
            ]["Labels"]["com.docker.compose.service"]
        for role, old in initial["identities"].items():
            if role != target_role and (
                final_native["identities"][role]["id"],
                final_native["identities"][role]["start"],
            ) != (old["id"], old["start"]):
                raise RuntimeError("unrelated survivor restarted: " + role)
        self.recorder.stop()
        resource_report = metrics.summarize(self.root / "resources.jsonl.gz")
        host = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        host_seconds = time.monotonic() - self.host_started
        host_cpu = (host.ru_utime - self.host_usage.ru_utime) + (
            host.ru_stime - self.host_usage.ru_stime
        )
        resource_report["host_controller"] = {
            "wall_seconds": host_seconds,
            "user_cpu_seconds": host.ru_utime - self.host_usage.ru_utime,
            "system_cpu_seconds": host.ru_stime - self.host_usage.ru_stime,
            "cpu_utilization_percent_one_core_100": host_cpu / host_seconds * 100,
            "child_cli_user_cpu_seconds": children.ru_utime - self.child_usage.ru_utime,
            "child_cli_system_cpu_seconds": children.ru_stime
            - self.child_usage.ru_stime,
            "process_lifetime_peak_rss_bytes": host.ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "limits": "Controller CPU deltas include witness reconciliation; child CPU is local CLI processes, not Docker VM/container work. Peak RSS is process-lifetime and can include earlier trials in the same invocation, not a per-trial or whole-stack peak. Docker daemon/VM overhead excluded.",
        }
        self.recorder = None
        self.phase("complete")
        lab.save(
            self.root / "result.json",
            {
                "passed": True,
                "operation": self.operation,
                "acknowledged": ack,
                "phases": self.phases,
                "proofs": proofs,
                "reports": reports,
                "postgres": sql_report,
                "resources": resource_report,
                "final_native": final_native,
            },
        )
        print(
            json.dumps({"passed": True, "case": self.id, "acknowledged": ack}),
            flush=True,
        )

    def failed(self, error):
        self.phase("failed", error=str(error))
        for side in ["source", "enriched"]:
            actor = self.root / side
            if actor.exists():
                (actor / "abort-reader").touch()
                if side == "source":
                    (actor / "abort-producer").touch()
        if self.fault:
            _, identity, policy = self.fault
            try:
                lab.call(["docker", "start", identity])
                lab.call(["docker", "update", "--restart=" + policy, identity])
            except Exception as restore:
                lab.save(self.root / "restore-failed.json", {"error": str(restore)})
        for obj in [self.probes, self.recorder]:
            if obj:
                try:
                    obj.stop()
                except Exception as cleanup:
                    lab.save(
                        self.root / ("cleanup-" + uuid.uuid4().hex + ".json"),
                        {"error": str(cleanup)},
                    )
        lab.save(self.root / "failure.json", {"passed": False, "error": str(error)})
        try:
            lab.diagnostics(self.root)
        except Exception as diagnostic:
            lab.save(self.root / "diagnostics-failed.json", {"error": str(diagnostic)})


def main():
    def terminate(signum, _frame):
        raise InterruptedError("experiment interrupted by signal " + str(signum))

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, terminate)
    lab.STATE.mkdir(exist_ok=True)
    with (lab.STATE / "experiment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        action = __import__("sys").argv[1]
        if action == "reconcile-prior":
            case = os.environ["CASE_ID"]
            if re.fullmatch(r"[0-9a-f]{32}", case) is None:
                raise ValueError("invalid case ID")
            root = lab.STATE / "cases" / case
            if not (root / "failure.json").exists():
                raise RuntimeError("expected a preserved failed/interrupted case")
            experiment = object.__new__(Experiment)
            proofs = {
                side: experiment.verify(case, side) for side in ("source", "enriched")
            }
            for proof in proofs.values():
                report = lab.STATE / proof["path"] / "verification.json"
                proof["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
            sql = probes.reconcile(root, json.loads((root / "phases.json").read_text()))
            check_negative()
            lab.save(
                root / "post-interruption-reconciliation.json",
                {
                    "original_status": "failed",
                    "time": time.time(),
                    "sha256": {
                        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in ("failure.json", "experiment.json")
                    },
                    "proofs": proofs,
                    "postgres": sql,
                },
            )
            print(
                json.dumps(
                    {"case": case, "original_status": "failed", "data_reconciled": True}
                ),
                flush=True,
            )
            return
        if action == "seal-setup":
            case = os.environ["CASE_ID"]
            if re.fullmatch(r"[0-9a-f]{32}", case) is None:
                raise ValueError("invalid case ID")
            setup_only_failure(lab.STATE / "cases" / case, seal=True)
            return
        for path in (lab.STATE / "cases").glob("*/experiment.json"):
            if (
                (
                    not (path.parent / "result.json").exists()
                    or (path.parent / "failure.json").exists()
                )
                and not setup_only_failure(path.parent)
                and not reconciled_failure(path.parent)
            ):
                raise RuntimeError(
                    "preserved unqualified case blocks new mutations: "
                    + str(path.parent)
                )
        actions = (
            ["baseline", "broker", "connect", "processor", "postgres"]
            if action == "test-production"
            else (["benchmark"] * 3 if action == "benchmark" else [action])
        )
        for operation in actions:
            experiment = Experiment(operation)
            try:
                experiment.run()
            except Exception as error:
                experiment.failed(error)
                raise


if __name__ == "__main__":
    main()
