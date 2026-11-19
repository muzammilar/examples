"""Serial Kafka operations with external witnesses and native recovery checks."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import cluster as lab
import report
import resources

BOOTSTRAP = "kafka-kafka-bootstrap:9092"


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
                    "replicas": [int(x) for x in fields["Replicas"].split(",")],
                    "isr": [int(x) for x in fields.get("Isr", "").split(",") if x],
                }
            )
    if not rows:
        raise ValueError("native topic description contains no partitions")
    if len({(r["topic"], r["partition"]) for r in rows}) != len(rows):
        raise ValueError("duplicate native partitions")
    return rows


def embedded_json(text):
    for line in text.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("missing native JSON response")


def identities(require_complete=True):
    pods = lab.json_kube(
        "get", "pods", "-l", "strimzi.io/name=kafka-kafka", "-o", "json"
    )["items"]
    result = {}
    for pod in pods:
        statuses = [
            s
            for s in pod.get("status", {}).get("containerStatuses", [])
            if s["name"] == "kafka"
        ]
        if not statuses or not statuses[0].get("containerID"):
            if require_complete:
                raise RuntimeError("Kafka container identity is unavailable")
            continue
        s = statuses[0]
        result[pod["metadata"]["name"]] = {
            "uid": pod["metadata"]["uid"],
            "node": pod["spec"]["nodeName"],
            "container": s["containerID"].removeprefix("containerd://"),
            "restarts": s["restartCount"],
            "ready": s["ready"],
        }
    if require_complete and len(result) != 6:
        raise RuntimeError(
            "expected exactly three dedicated controllers and three brokers"
        )
    return result


def cli(tool, *args, pod=None, timeout=90):
    if pod is None:
        candidates = [
            n for n, s in identities(False).items() if "-brokers-" in n and s["ready"]
        ]
        if not candidates:
            raise RuntimeError("no healthy broker for native inspection")
        pod = sorted(candidates)[-1]
    return lab.call(
        lab.KUBE
        + [
            "exec",
            pod,
            "-c",
            "kafka",
            "--",
            "/opt/kafka/bin/" + tool,
            "--bootstrap-server",
            BOOTSTRAP,
        ]
        + list(args),
        timeout=timeout,
    )


def topics():
    return [
        json.loads(p.read_text())["topic"]
        for p in sorted(lab.WITNESS.glob("*/case.json"))
    ]


def native():
    ids = identities(False)
    if len(ids) != 6 or not all(s["ready"] for s in ids.values()):
        return {"time": time.time(), "identities": ids, "transitional": True}
    description = cli("kafka-topics.sh", "--describe")
    rows = partitions(description)
    owned = set(topics())
    ours = [r for r in rows if r["topic"] in owned]
    quorum = cli("kafka-metadata-quorum.sh", "describe", "--status")
    logs = embedded_json(
        cli("kafka-log-dirs.sh", "--describe", "--broker-list", "3,4,5")
    )
    ids = identities()
    return {
        "time": time.time(),
        "partitions": ours,
        "description": description,
        "quorum": quorum,
        "log_dirs": logs,
        "identities": ids,
    }


def healthy(snapshot):
    if snapshot.get("transitional"):
        return False
    rows = snapshot["partitions"]
    expected = set(topics())
    if {(r["topic"], r["partition"]) for r in rows} != {
        (t, p) for t in expected for p in range(6)
    }:
        return False
    if any(
        set(r["replicas"]) != {3, 4, 5}
        or set(r["isr"]) != {3, 4, 5}
        or r["leader"] not in {3, 4, 5}
        for r in rows
    ):
        return False
    if not all(s["ready"] for s in snapshot["identities"].values()):
        return False
    brokers = snapshot["log_dirs"]["brokers"]
    if {b["broker"] for b in brokers} != {3, 4, 5}:
        return False
    for broker in brokers:
        found = set()
        for directory in broker["logDirs"]:
            if directory["error"] is not None:
                return False
            for p in directory["partitions"]:
                if any(p["partition"].startswith(t + "-") for t in expected):
                    if p["offsetLag"] != 0 or p.get("isFuture", False):
                        return False
                    found.add(p["partition"])
        if found != {t + "-" + str(p) for t in expected for p in range(6)}:
            return False
    quorum = snapshot["quorum"]
    voters = re.search(r"^CurrentVoters:\s*(.+)$", quorum, re.MULTILINE)
    lag = re.search(r"^MaxFollowerLag:\s*(\d+)$", quorum, re.MULTILINE)
    return (
        bool(re.search(r"LeaderId:\s*[012]\b", quorum))
        and bool(voters)
        and {v["id"] for v in json.loads(voters[1])} == {0, 1, 2}
        and bool(lag)
        and int(lag[1]) == 0
    )


def complete_job(actor, root, timeout=300):
    def completed():
        job = lab.json_kube("get", "job", actor["job"], "-o", "json")
        status = job.get("status", {})
        if status.get("failed", 0) or any(
            c["type"] == "Failed" and c["status"] == "True"
            for c in status.get("conditions", [])
        ):
            raise RuntimeError("workload Job failed: " + actor["job"])
        return status.get("succeeded", 0) == 1

    lab.wait(completed, "workload Job completion: " + actor["job"], timeout)
    text = lab.call(lab.KUBE + ["logs", actor["pod"], "-c", actor["role"]])
    (root / (actor["job"] + ".log")).write_text(text)


DIAGNOSTIC_STATE_FIELDS = [
    "acknowledged",
    "observed_acknowledged",
    "acknowledged_loss",
    "corruption",
    "duplicate_acknowledged_records",
    "duplicate_logical_records",
    "unique_messages",
    "observed_nonack",
    "ambiguous_intents",
    "physical_records",
    "partition_sha256",
    "start_offsets",
    "end_offsets",
    "positions",
    "run_id",
    "topic",
    "diagnostic_order_errors",
]


def diagnostic_state(proof):
    if (
        proof.get("diagnostic_only") is not True
        or proof.get("strict_verification") is not False
    ):
        raise RuntimeError("expected a diagnostic, never a strict passing result")
    if any(
        proof[key]
        for key in [
            "acknowledged_loss",
            "corruption",
            "duplicate_acknowledged_records",
            "poll_errors",
        ]
    ):
        raise RuntimeError(
            "cannot baseline lost/corrupt/duplicated acknowledged data or an incomplete diagnostic"
        )
    if (
        proof["acknowledged"] != proof["observed_acknowledged"]
        or proof["positions"] != proof["end_offsets"]
    ):
        raise RuntimeError(
            "diagnosis did not reconcile all ACKs through native end offsets"
        )
    return {key: proof[key] for key in DIAGNOSTIC_STATE_FIELDS}


def diagnose_history(case):
    output = lab.RESULTS / "cases" / case / "diagnoses" / uuid.uuid4().hex
    actor = lab.job("verify", case, output, mode="diagnose")
    lab.save(output / "job.json", actor)
    complete_job(actor, output, 600)
    return output, json.loads((output / "diagnosis.json").read_text())


def freeze_failed_history(case):
    root = lab.RESULTS / "cases" / case
    if not (root / "failure.json").exists() or (root / "result.json").exists():
        raise RuntimeError(
            "only an explicitly failed history may get a failed-state baseline"
        )
    for role in ["producer", "reader"]:
        job = lab.json_kube("get", "job", role + "-" + case[:16], "-o", "json")
        status = job.get("status", {})
        if status.get("active", 0) or not (
            status.get("failed", 0) or status.get("succeeded", 0)
        ):
            raise RuntimeError("failed-history workload has not terminated")
    if not healthy(native()):
        raise RuntimeError("native repair must finish before freezing a failed state")
    first_path, first = diagnose_history(case)
    second_path, second = diagnose_history(case)
    state = diagnostic_state(first)
    if diagnostic_state(second) != state:
        raise RuntimeError("independent full scans disagree on failed physical state")
    lab.save(
        root / "known-failure-baseline.json",
        {
            "original_status": "failed",
            "time": time.time(),
            "reason": "Observed extra physical copies among ambiguous sends; original strict test remains failed",
            "first_diagnosis": str(first_path),
            "second_diagnosis": str(second_path),
            "state": state,
        },
    )
    print(
        "Recorded FAILED history state; this is not a passing test: " + case, flush=True
    )


class Experiment:
    def __init__(self, scenario):
        self.id = uuid.uuid4().hex
        self.root = lab.RESULTS / "cases" / self.id
        self.root.mkdir(parents=True)
        self.witness = lab.WITNESS / self.id
        self.witness.mkdir()
        self.scenario = scenario
        self.actors = []
        self.phases = []
        self.restore = None
        self.recorder = resources.Recorder(self.root)
        count = int(os.environ.get("COUNT", "1000000"))
        if not 1 <= count <= 2_000_000:
            raise ValueError("COUNT must be 1..2000000")
        self.plan = {
            "format_version": 1,
            "run_id": self.id,
            "topic": "orders-" + self.id,
            "bootstrap_servers": BOOTSTRAP,
            "minimum_messages": count,
            "warmup_messages": max(1, count // 2),
            "live_rate": int(os.environ.get("LIVE_RATE", "1000")),
            "burst_rate": int(os.environ.get("BURST_RATE", "0")),
            "duration_seconds": int(os.environ.get("DURATION_SECONDS", "1800")),
            "delivery_timeout_ms": int(os.environ.get("DELIVERY_TIMEOUT_MS", "300000")),
            "partitions": 6,
            "group_id": "reader-" + self.id,
        }
        lab.save(self.witness / "case.json", self.plan)
        lab.save(
            self.root / "setup.json",
            {
                "scenario": scenario,
                "started": time.time(),
                "owner": lab.owner(),
                "build": json.loads((lab.STATE / "build-images.json").read_text()),
            },
        )

    def phase(self, name, **extra):
        entry = {"phase": name, "time": time.time(), **extra}
        self.phases.append(entry)
        lab.save(self.root / "phases.json", self.phases, replace=True)
        print(json.dumps(entry), flush=True)

    def check_actors(self):
        if hasattr(self, "recorder"):
            self.recorder.check()
        for actor in self.actors:
            data = lab.json_kube("get", "pod", actor["pod"], "-o", "json")
            for s in data.get("status", {}).get("containerStatuses", []):
                exited = s.get("state", {}).get("terminated")
                if exited and exited["exitCode"] != 0:
                    raise RuntimeError(f"{actor['role']} exited: {exited}")
            if (self.witness / "failure.json").exists():
                raise RuntimeError((self.witness / "failure.json").read_text())

    def progress(self):
        self.check_actors()
        values = []
        for name in ["producer-progress.json", "reader-progress.json"]:
            path = self.witness / name
            if not path.exists():
                return None
            value = json.loads(path.read_text())
            if value["run_id"] != self.id or time.time() - value["time"] > 20:
                raise RuntimeError("stale or foreign live workload progress")
            values.append(value)
        return values

    def live_progress(self, seconds=10):
        before = lab.wait(self.progress, "live workload checkpoint", 60)
        time.sleep(seconds)
        after = self.progress()
        if (
            not after
            or after[0]["acknowledged"] <= before[0]["acknowledged"]
            or after[1]["unique_messages"] <= before[1]["unique_messages"]
        ):
            raise RuntimeError(
                "independent writer and reader did not both make progress"
            )
        lab.save(
            self.root / ("progress-" + str(time.time_ns()) + ".json"),
            {"before": before, "after": after},
        )

    def repaired(self, timeout=600):
        def check():
            self.check_actors()
            snapshot = native()
            lab.save(self.root / ("native-" + str(time.time_ns()) + ".json"), snapshot)
            if not healthy(snapshot):
                return None
            identities_path = lab.RESULTS / "native-identities.json"
            observed = dict(
                re.findall(
                    r"Topic:\s*(\S+)\s+TopicId:\s*(\S+)\s+PartitionCount:",
                    snapshot["description"],
                )
            )
            cluster_id = re.search(
                r"^ClusterId:\s*(\S+)$", snapshot["quorum"], re.MULTILINE
            )
            if not cluster_id or not set(topics()).issubset(observed):
                raise RuntimeError("native cluster or topic identities are incomplete")
            old = (
                json.loads(identities_path.read_text())
                if identities_path.exists()
                else {"cluster_id": cluster_id[1], "topics": {}}
            )
            if old["cluster_id"] != cluster_id[1] or any(
                observed.get(t) != identity for t, identity in old["topics"].items()
            ):
                raise RuntimeError("retained cluster or topic identity changed")
            old["topics"].update({t: observed[t] for t in topics()})
            lab.save(identities_path, old, replace=True)
            return snapshot

        return lab.wait(check, "native ISR and all-broker log recovery", timeout)

    def complete_job(self, actor, timeout=300):
        complete_job(actor, self.root, timeout)

    def crash(self, target):
        before = identities()
        old = before[target]
        node = lab.docker_node(old["node"])
        inspection = json.loads(
            lab.call(
                ["docker", "exec", node["Id"], "crictl", "inspect", old["container"]]
            )
        )
        lab.save(self.root / "crash-before.json", {"pods": before, "cri": inspection})
        status = inspection["status"]
        if (
            status["id"] != old["container"]
            or status["state"] != "CONTAINER_RUNNING"
            or status["labels"]["io.kubernetes.pod.uid"] != old["uid"]
        ):
            raise RuntimeError("CRI target identity is not the native role owner")
        pid = int(inspection["info"]["pid"])
        if pid <= 1:
            raise RuntimeError("refusing unsafe PID")
        cgroup = lab.call(["docker", "exec", node["Id"], "cat", f"/proc/{pid}/cgroup"])
        if old["container"] not in cgroup:
            raise RuntimeError("CRI PID does not belong to the exact old container")
        self.phase("fault", target=target, container=old["container"], pid=pid)
        lab.call(["docker", "exec", node["Id"], "kill", "-KILL", str(pid)])

        def exited():
            records = json.loads(
                lab.call(
                    ["docker", "exec", node["Id"], "crictl", "ps", "-a", "-o", "json"]
                )
            )
            matches = [c for c in records["containers"] if c["id"] == old["container"]]
            lab.save(self.root / ("cri-" + str(time.time_ns()) + ".json"), records)
            return not matches or matches[0]["state"] == "CONTAINER_EXITED"

        lab.wait(exited, "positive old process fencing", 60)

        def replaced():
            current = identities(False)
            return (
                current
                if target in current
                and len(current) == 6
                and current[target]["container"] != old["container"]
                and current[target]["ready"]
                else None
            )

        after = lab.wait(replaced, "native container restart", 240)
        if any(
            after[name]["container"] != info["container"]
            for name, info in before.items()
            if name != target
        ):
            raise RuntimeError("unrelated Kafka survivor restarted")
        lab.save(self.root / "crash-after.json", after)
        self.phase("process-restarted", target=target)

    def native_json_file(self, value, pod):
        path = "/tmp/lab-" + uuid.uuid4().hex + ".json"
        lab.call(
            lab.KUBE
            + [
                "exec",
                "-i",
                pod,
                "-c",
                "kafka",
                "--",
                "sh",
                "-ec",
                'umask 077; cat > "$1"',
                "lab",
                path,
            ],
            input_text=json.dumps(value),
        )
        return path

    def reassign(self, entries, pod):
        value = {"version": 1, "partitions": entries}
        path = self.native_json_file(value, pod)
        text = cli(
            "kafka-reassign-partitions.sh",
            "--execute",
            "--reassignment-json-file",
            path,
            pod=pod,
        )
        lab.save(
            self.root / ("reassignment-" + str(time.time_ns()) + ".json"),
            {"request": value, "response": text},
        )
        return path

    def disk_failure(self, snapshot):
        before = snapshot["identities"]
        pool = lab.json_kube("get", "kafkanodepool", "brokers", "-o", "json")
        volume_ids = {v["id"] for v in pool["spec"]["storage"]["volumes"]}
        if volume_ids == {0}:
            self.phase("add-log-directory")
            lab.call(lab.KUBE + ["apply", "-f", "manifests/kafka-two-disks.yaml"])

            def rolled():
                current = identities(False)
                return len(current) == 6 and all(
                    current[n]["uid"] != before[n]["uid"] and current[n]["ready"]
                    for n in before
                    if "-brokers-" in n
                )

            lab.wait(
                rolled, "all broker Pods reconcile added ephemeral log directory", 600
            )
            snapshot = self.repaired()
            after = snapshot["identities"]
            if any(
                before[n]["uid"] != after[n]["uid"]
                for n in before
                if "-controllers-" in n
            ):
                raise RuntimeError(
                    "controller unexpectedly recreated during broker storage change"
                )
        elif volume_ids == {0, 1}:
            self.phase("reuse-two-log-directories")
        else:
            raise RuntimeError("unexpected JBOD storage; no mutation authorized")
        leader = next(
            r["leader"]
            for r in snapshot["partitions"]
            if r["topic"] == self.plan["topic"] and r["partition"] == 0
        )
        pod = "kafka-brokers-" + str(leader)
        target = next(
            b for b in snapshot["log_dirs"]["brokers"] if b["broker"] == leader
        )
        directories = [
            d["logDir"]
            for d in target["logDirs"]
            if re.fullmatch(r"/var/lib/kafka/data-1/kafka-log[0-9]+", d["logDir"])
        ]
        if len(directories) != 1:
            raise RuntimeError("native second log directory unavailable")
        directory = directories[0]
        config = lab.call(
            lab.KUBE
            + ["exec", pod, "-c", "kafka", "--", "cat", "/tmp/strimzi.properties"]
        )
        metadata = re.search(r"^metadata.log.dir=(.+)$", config, re.MULTILINE)
        if not metadata or not metadata[1].startswith("/var/lib/kafka/data-0/"):
            raise RuntimeError("metadata log must be verified on untouched volume 0")
        lab.save(
            self.root / "disk-metadata-location.json",
            {"directory": directory, "metadata_log_dir": metadata[1]},
        )
        entries = [
            {
                "topic": r["topic"],
                "partition": r["partition"],
                "replicas": r["replicas"],
                "log_dirs": [
                    directory if b == leader else "any" for b in r["replicas"]
                ],
            }
            for r in snapshot["partitions"]
            if r["topic"] == self.plan["topic"]
        ]
        self.phase("move-replicas-to-log-directory")
        self.reassign(entries, pod)

        def moved():
            state = self.repaired()
            b = next(b for b in state["log_dirs"]["brokers"] if b["broker"] == leader)
            d = next(d for d in b["logDirs"] if d["logDir"] == directory)
            return (
                len(
                    [
                        p
                        for p in d["partitions"]
                        if p["partition"].startswith(self.plan["topic"] + "-")
                        and p["size"] > 0
                        and p["offsetLag"] == 0
                    ]
                )
                == 6
            )

        lab.wait(moved, "native replica movement onto selected disk", 300)
        self.live_progress()
        text = lab.call(
            lab.KUBE
            + [
                "exec",
                pod,
                "-c",
                "kafka",
                "--",
                "find",
                directory,
                "-type",
                "d",
                "-printf",
                "%m %p\n",
            ]
        )
        modes = []
        for line in text.splitlines():
            mode, path = line.split(" ", 1)
            if (
                not re.fullmatch("[0-7]{3,4}", mode)
                or not (path == directory or path.startswith(directory + "/"))
                or any(c in path for c in "\n\r")
            ):
                raise RuntimeError("unsafe directory mode record")
            modes.append((mode, path))
        if not modes:
            raise RuntimeError("no owned directories to inject")
        lab.save(self.root / "disk-original-modes.json", modes)

        def restore():
            for mode, path in modes:
                lab.call(
                    lab.KUBE + ["exec", pod, "-c", "kafka", "--", "chmod", mode, path]
                )

        self.restore = restore
        self.phase(
            "fault",
            target=pod,
            log_directory=directory,
            kind="directory write permission denied",
        )
        lab.call(
            lab.KUBE
            + [
                "exec",
                pod,
                "-c",
                "kafka",
                "--",
                "find",
                directory,
                "-type",
                "d",
                "-exec",
                "chmod",
                "a-w",
                "{}",
                "+",
            ]
        )

        def offline():
            state = native()
            lab.save(
                self.root / ("disk-offline-" + str(time.time_ns()) + ".json"), state
            )
            b = next(b for b in state["log_dirs"]["brokers"] if b["broker"] == leader)
            d = next(d for d in b["logDirs"] if d["logDir"] == directory)
            isr_loss = any(
                leader not in r["isr"]
                for r in state["partitions"]
                if r["topic"] == self.plan["topic"]
            )
            return d["error"] is not None and isr_loss

        lab.wait(offline, "fresh native offline log-directory and ISR loss", 180)
        self.phase("disk-offline-confirmed")
        self.live_progress(30)
        restore()
        self.restore = None
        self.phase("restore-permissions")
        self.crash(pod)
        self.repaired()
        # Prefer the repaired broker for all retained histories; the replay must
        # fetch complete bodies from this member, rather than a surviving leader.
        state = native()
        preferred = [
            {
                "topic": r["topic"],
                "partition": r["partition"],
                "replicas": [leader] + [b for b in r["replicas"] if b != leader],
            }
            for r in state["partitions"]
        ]
        self.reassign(preferred, pod)
        self.repaired()
        election = self.native_json_file(
            {
                "partitions": [
                    {"topic": r["topic"], "partition": r["partition"]}
                    for r in preferred
                ]
            },
            pod,
        )
        response = cli(
            "kafka-leader-election.sh",
            "--election-type",
            "preferred",
            "--path-to-json-file",
            election,
            pod=pod,
        )
        lab.save(
            self.root / "repaired-leader-election.json",
            {"target": leader, "response": response},
        )
        lab.wait(
            lambda: all(r["leader"] == leader for r in native()["partitions"]),
            "repaired member leads every retained partition",
            180,
        )
        self.phase("repaired-broker-serving", broker=leader)

    def replay(self):
        proofs = []
        for source in sorted(lab.WITNESS.glob("*/case.json")):
            if not (source.parent / "producer.json").exists():
                failure = lab.RESULTS / "cases" / source.parent.name / "failure.json"
                if (
                    not failure.exists()
                    or not (source.parent / "producer.jsonl.gz").exists()
                ):
                    raise RuntimeError(
                        "incomplete undiagnosed producer witness: " + str(source)
                    )
                terminal = lab.json_kube(
                    "get", "job", "producer-" + source.parent.name[:16], "-o", "json"
                )
                if not terminal.get("status", {}).get("failed", 0):
                    raise RuntimeError(
                        "partial producer witness lacks native failed Job proof"
                    )
                # Unmatched durable intents stay ambiguous; replay never invents
                # acknowledgements from their observed bodies.
            output = lab.RESULTS / "cases" / source.parent.name / "replays" / self.id
            baseline_path = (
                lab.RESULTS
                / "cases"
                / source.parent.name
                / "known-failure-baseline.json"
            )
            baseline = (
                json.loads(baseline_path.read_text())
                if baseline_path.exists()
                else None
            )
            actor = lab.job(
                "verify",
                source.parent.name,
                output,
                mode="diagnose" if baseline else "verify",
            )
            lab.save(output / "job.json", actor)
            self.complete_job(actor, 600)
            proof = json.loads(
                (
                    output / ("diagnosis.json" if baseline else "verification.json")
                ).read_text()
            )
            if baseline:
                if (
                    baseline.get("original_status") != "failed"
                    or diagnostic_state(proof) != baseline["state"]
                ):
                    raise RuntimeError(
                        "known FAILED history changed; do not overwrite its baseline"
                    )
                print(
                    "Historical test remains FAILED; exact physical state unchanged: "
                    + source.parent.name,
                    flush=True,
                )
            elif (
                proof.get("strict_verification") is not True
                or proof["acknowledged_loss"]
                or proof["corruption"]
                or proof["acknowledged"] != proof["observed_acknowledged"]
            ):
                raise RuntimeError(
                    "native replay violated acknowledged message contract"
                )
            proofs.append(
                {
                    "source": source.parent.name,
                    "path": str(output),
                    "result": proof,
                    "historical_status": "failed"
                    if baseline
                    else "strict_replay_passed",
                }
            )
        return proofs

    def execute(self):
        self.recorder.start()
        self.phase("bootstrap")
        source_files = [
            lab.ROOT / n
            for n in [
                "Makefile",
                "cluster.py",
                "control.py",
                "report.py",
                "resources.py",
            ]
        ]
        source_files += sorted((lab.ROOT / "manifests").glob("*.yaml"))
        hashes = {}
        for source in source_files:
            relative = source.relative_to(lab.ROOT)
            target = self.root / "sources" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            hashes[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
        lab.save(self.root / "controller-sources.json", hashes)
        topic = {
            "apiVersion": "kafka.strimzi.io/v1",
            "kind": "KafkaTopic",
            "metadata": {
                "name": self.plan["topic"],
                "namespace": lab.NAMESPACE,
                "labels": {
                    "strimzi.io/cluster": "kafka",
                    lab.LABEL: lab.owner()["owner"],
                },
            },
            "spec": {
                "partitions": 6,
                "replicas": 3,
                "config": {
                    "min.insync.replicas": 2,
                    "retention.ms": -1,
                    "segment.bytes": 1048576,
                    "segment.ms": 5000,
                    "segment.jitter.ms": 0,
                },
            },
        }
        lab.call(lab.KUBE + ["create", "-f", "-"], input_text=json.dumps(topic))
        lab.call(
            lab.KUBE
            + [
                "wait",
                "kafkatopic/" + self.plan["topic"],
                "--for=condition=Ready",
                "--timeout=180s",
            ],
            timeout=200,
        )
        self.repaired()
        self.actors.append(lab.job("reader", self.id))
        self.actors.append(lab.job("producer", self.id))
        lab.save(self.root / "actors.json", self.actors)

        def warm():
            value = self.progress()
            return (
                value
                and value[0]["acknowledged"] >= self.plan["warmup_messages"]
                and value[1]["unique_messages"] >= self.plan["warmup_messages"] // 2
            )

        lab.wait(warm, "half-dataset warmup with independent reader", 600)
        self.phase("baseline")
        self.live_progress()
        snapshot = self.repaired()
        if self.scenario == "broker-crash":
            leader = next(
                r["leader"]
                for r in snapshot["partitions"]
                if r["topic"] == self.plan["topic"] and r["partition"] == 0
            )
            self.crash("kafka-brokers-" + str(leader))
        elif self.scenario == "controller-crash":
            leader = int(re.search(r"LeaderId:\s*(\d+)", snapshot["quorum"])[1])
            self.crash("kafka-controllers-" + str(leader))
        elif self.scenario == "disk-failure":
            self.disk_failure(snapshot)
        self.phase("native-recovery")
        self.repaired()
        self.live_progress()
        self.phase("recovered")
        (self.witness / "finish-producer").touch(exist_ok=False)
        producer = next(a for a in self.actors if a["role"] == "producer")
        self.complete_job(producer, 600)
        summary = json.loads((self.witness / "producer.json").read_text())
        if summary["acknowledged"] < self.plan["minimum_messages"] or summary.get(
            "aborted"
        ):
            raise RuntimeError("minimum acknowledged workload not reached")

        def caught_up():
            self.check_actors()
            p = json.loads((self.witness / "reader-progress.json").read_text())
            return p["unique_messages"] >= summary["acknowledged"]

        lab.wait(caught_up, "live consumer final catch-up", 300)
        proofs = self.replay()
        self.check_actors()
        (self.witness / "stop-reader").touch(exist_ok=False)
        self.complete_job(next(a for a in self.actors if a["role"] == "reader"))
        self.repaired()
        offsets = cli(
            "kafka-consumer-groups.sh", "--describe", "--group", self.plan["group_id"]
        )
        (self.root / "native-final-group-offsets.txt").write_text(offsets)
        matched = {}
        for line in offsets.splitlines():
            fields = line.split()
            if (
                len(fields) >= 6
                and fields[0] == self.plan["group_id"]
                and fields[1] == self.plan["topic"]
            ):
                p, committed, end, lag = map(int, fields[2:6])
                if committed != end or lag != 0 or p in matched:
                    raise RuntimeError(
                        "consumer native committed offsets have not caught up"
                    )
                matched[p] = end
        final_reader = json.loads((self.witness / "reader.json").read_text())
        if set(matched) != set(range(6)) or any(
            final_reader["offsets"].get(str(p)) != end for p, end in matched.items()
        ):
            raise RuntimeError("consumer final offsets differ from native group")
        self.recorder.stop()
        lab.save(
            self.root / "resource-summary.json",
            resources.summarize(self.root / "resources.jsonl.gz"),
        )
        self.phase("complete")
        measurements = report.reconcile(self.witness, self.plan, self.phases)
        lab.save(self.root / "measurements.json", measurements)
        lab.save(
            self.root / "result.json",
            {
                "status": "passed",
                "producer": summary,
                "proofs": proofs,
                "reader": json.loads((self.witness / "reader.json").read_text()),
            },
        )

    def diagnostics(self, error):
        try:
            self.recorder.stop()
        except RuntimeError as detail:
            lab.save(
                self.root / "resource-recorder-failure.json", {"error": str(detail)}
            )
        lab.save(
            self.root / "failure.json", {"time": time.time(), "error": repr(error)}
        )
        if self.restore is not None:
            try:
                self.restore()
            except RuntimeError as detail:
                lab.save(
                    self.root / "fault-restore-failure.json", {"error": str(detail)}
                )
        for name in ["abort-producer", "abort-reader"]:
            (self.witness / name).touch(exist_ok=True)
        for name, arguments in [
            ("pods", ["get", "pods", "-o", "json"]),
            ("events", ["get", "events", "-o", "json"]),
            ("kafka", ["get", "kafka", "kafka", "-o", "json"]),
        ]:
            try:
                text = lab.call(lab.KUBE + arguments, timeout=30)
                (self.root / (name + "-failure.json")).write_text(text)
            except (RuntimeError, subprocess.TimeoutExpired) as detail:
                (self.root / (name + "-diagnostic-error.txt")).write_text(str(detail))
        for actor in self.actors:
            try:
                text = lab.call(
                    lab.KUBE + ["logs", actor["pod"], "-c", actor["role"]], timeout=30
                )
                (self.root / (actor["job"] + "-failure.log")).write_text(text)
            except (RuntimeError, subprocess.TimeoutExpired) as detail:
                (self.root / (actor["job"] + "-diagnostic-error.txt")).write_text(
                    str(detail)
                )


def teardown(action):
    ownership = lab.owner()
    if action == "down":
        cases = list(lab.WITNESS.glob("*/case.json"))
        for source in cases:
            result = lab.RESULTS / "cases" / source.parent.name / "result.json"
            failed = result.parent / "failure.json"
            if not result.exists() and not failed.exists():
                raise RuntimeError(
                    "incomplete history has no terminal result: " + str(source)
                )
            if not (source.parent / "producer.json").exists() and not failed.exists():
                raise RuntimeError("producer witness is incomplete: " + str(source))
            # Failed histories remain failed. The fresh pre-teardown replay below
            # must independently reconcile their ACKs before storage is removed.
        audit = Experiment.__new__(Experiment)
        audit.id = uuid.uuid4().hex
        audit.root = lab.RESULTS / "teardown" / audit.id
        audit.root.mkdir(parents=True)
        audit.actors = []
        if cases:
            state = native()
            lab.save(audit.root / "native-before-down.json", state)
            if not healthy(state):
                raise RuntimeError("native redundancy is not restored before teardown")
            proofs = audit.replay()
            lab.save(audit.root / "historical-proofs.json", proofs)
        namespace = lab.json_kube("get", "namespace", lab.NAMESPACE, "-o", "json")
        if namespace["metadata"].get("labels", {}).get(lab.LABEL) != ownership["owner"]:
            raise RuntimeError("namespace ownership differs")
        lab.call(
            lab.KUBE
            + [
                "delete",
                "jobs",
                "-l",
                lab.LABEL + "=" + ownership["owner"],
                "--wait=true",
            ],
            timeout=180,
        )
        lab.call(
            lab.KUBE
            + [
                "delete",
                "kafkatopic",
                "-l",
                lab.LABEL + "=" + ownership["owner"],
                "--wait=true",
            ],
            timeout=180,
        )
        lab.call(
            lab.KUBE + ["delete", "kafka", "kafka", "--wait=true", "--timeout=300s"],
            timeout=320,
        )
        lab.call(
            lab.KUBE
            + [
                "delete",
                "kafkanodepool",
                "brokers",
                "controllers",
                "--ignore-not-found",
                "--wait=true",
            ],
            timeout=180,
        )
        lab.call(
            [
                "helm",
                "--kubeconfig",
                str(lab.CONFIG),
                "--kube-context",
                lab.CONTEXT,
                "uninstall",
                "strimzi-operator",
                "--namespace",
                lab.NAMESPACE,
                "--wait",
                "--timeout",
                "180s",
            ],
            timeout=200,
        )
        lab.call(
            lab.KUBE
            + ["delete", "namespace", lab.NAMESPACE, "--wait=true", "--timeout=180s"],
            timeout=200,
        )
        lab.save(
            lab.STATE / "down.json",
            {
                "time": time.time(),
                "owner": ownership["owner"],
                "audit": str(audit.root),
            },
        )
        return
    receipt = json.loads((lab.STATE / "down.json").read_text())
    if receipt["owner"] != ownership["owner"]:
        raise RuntimeError("down receipt belongs to another lab")
    if lab.call(
        lab.KUBE
        + ["get", "namespace", lab.NAMESPACE, "--ignore-not-found", "-o", "name"]
    ).strip():
        raise RuntimeError("example namespace still exists; run down first")
    lab.call(
        [
            "kind",
            "delete",
            "cluster",
            "--name",
            lab.CLUSTER,
            "--kubeconfig",
            str(lab.CONFIG),
        ],
        timeout=180,
    )
    remaining = lab.call(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}"]
    ).splitlines()
    if any(node in remaining for node in ownership["nodes"].values()):
        raise RuntimeError("kind node containers remain")
    for role, info in json.loads((lab.STATE / "build-images.json").read_text())[
        "images"
    ].items():
        image = "kafka-strimzi-" + role + ":dev"
        value = json.loads(lab.call(["docker", "image", "inspect", image]))[0]
        if (
            value["Id"] != info["id"]
            or value["Config"].get("Labels", {}).get("lab.example.system")
            != lab.CLUSTER
        ):
            raise RuntimeError("workload image ownership changed")
        if lab.call(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "ancestor=" + image,
                "--format",
                "{{.ID}}",
            ]
        ).strip():
            raise RuntimeError("workload image remains in use")
        lab.call(["docker", "image", "rm", image])
    archive = lab.STATE / "archives" / ownership["owner"]
    archive.mkdir(parents=True, exist_ok=False)
    for path in [
        lab.WITNESS,
        lab.RESULTS,
        lab.STATE / "owner.json",
        lab.STATE / "creation-intent.json",
        lab.STATE / "down.json",
        lab.STATE / "build-images.json",
    ]:
        path.rename(archive / path.name)
    extras = [
        lab.STATE / name
        for name in [
            "benchmark-platform.json",
            "clock-offset-probe.json",
            "core-production-numbers.json",
            "benchmark-numbers.json",
            "foreign-volumes-before-cleanup.json",
            "retained-setup-before.json",
            "retained-setup-after.json",
        ]
    ]
    extras += sorted(lab.STATE.glob("build-*.log"))
    extras += sorted(lab.STATE.glob("build-images-*.json"))
    for path in extras:
        if path.exists():
            path.rename(archive / path.name)
    if lab.CONFIG.exists():
        lab.CONFIG.unlink()
    generated = lab.STATE / "kind-config.yaml"
    if generated.exists():
        generated.unlink()
    lab.save(
        archive / "cleanup.json",
        {
            "time": time.time(),
            "cluster_deleted": True,
            "private_kubeconfig_removed": True,
            "owned_workload_images_removed": True,
        },
    )

    checksums = {}
    for path in sorted(archive.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                checksums[str(path.relative_to(archive))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    lab.save(archive / "checksums.json", checksums)
    for name, digest in checksums.items():
        with (archive / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("archived evidence hash differs: " + name)
    print(
        "Archived and hash-verified " + str(len(checksums)) + " files: " + str(archive),
        flush=True,
    )


def remove_added_disks():
    lab.owner()
    plan = lab.json_kube(
        "get",
        "kafkarebalance",
        os.environ.get("REBALANCE", "kafka-jbod-rebalance"),
        "-o",
        "json",
    )
    targets = plan.get("spec", {}).get("moveReplicasOffVolumes", [])
    if (
        plan.get("spec", {}).get("mode") != "remove-disks"
        or {t["brokerId"] for t in targets} != {3, 4, 5}
        or any(t["volumeIds"] != [1] for t in targets)
    ):
        raise RuntimeError(
            "expected a completed removal plan for volume 1 on brokers 3,4,5"
        )
    if not any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in plan.get("status", {}).get("conditions", [])
    ):
        raise RuntimeError("disk removal plan is not Ready")
    state = native()
    if not healthy(state):
        raise RuntimeError("native redundancy is not restored")
    for broker in state["log_dirs"]["brokers"]:
        added = [
            d
            for d in broker["logDirs"]
            if re.fullmatch(r"/var/lib/kafka/data-1/kafka-log[0-9]+", d["logDir"])
        ]
        if len(added) != 1 or added[0]["error"] is not None or added[0]["partitions"]:
            raise RuntimeError(
                "volume 1 still owns data or its native state is unknown; refusing removal"
            )
    lab.save(
        lab.RESULTS / "manual-disk-removals" / (uuid.uuid4().hex + ".json"),
        {"plan": plan, "native": state},
    )
    lab.call(lab.KUBE + ["apply", "-f", "manifests/kafka-one-disk.yaml"])
    lab.call(
        lab.KUBE + ["wait", "kafka/kafka", "--for=condition=Ready", "--timeout=600s"],
        timeout=620,
    )


def main():
    action = sys.argv[1]
    if action in ("down", "kind-down"):
        teardown(action)
        return
    lab.owner()
    if action == "remove-added-disks":
        remove_added_disks()
        return
    if action == "diagnose-history":
        output, proof = diagnose_history(os.environ["CASE_ID"])
        print(json.dumps(proof), flush=True)
        print("Diagnostic only, not a passing verification: " + str(output), flush=True)
        return
    if action == "freeze-failed-history":
        freeze_failed_history(os.environ["CASE_ID"])
        return
    if action == "verify-history":
        audit = Experiment.__new__(Experiment)
        audit.id = uuid.uuid4().hex
        audit.root = lab.RESULTS / "audits" / audit.id
        audit.root.mkdir(parents=True)
        audit.actors = []
        audit.repaired()
        lab.save(audit.root / "proofs.json", audit.replay())
        print("All retained histories verified: " + str(audit.root), flush=True)
        return
    if shutil.disk_usage(lab.ROOT).free < 4 * 1024**3:
        raise RuntimeError("less than 4 GiB host free space; no fault authorized")
    if action not in (
        "correctness",
        "benchmark",
        "broker-crash",
        "controller-crash",
        "disk-failure",
    ):
        raise ValueError("unimplemented action: " + action)
    experiment = Experiment(action)
    try:
        experiment.execute()
    except BaseException as error:
        experiment.diagnostics(error)
        raise


if __name__ == "__main__":
    lab.STATE.mkdir(exist_ok=True)
    with (lab.STATE / "lifecycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
