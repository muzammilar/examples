"""Serial live MirrorMaker failures with external witnesses and native repair barriers."""

import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid

import replication_lab as lab
import replication_report as report
import replication_resources as resources


def identities():
    result = {}
    for pod in lab.kube("get", "pods", "-o", "json")["items"]:
        labels = pod["metadata"].get("labels", {})
        if (
            not any(key.startswith("strimzi.io/") for key in labels)
            or "job-name" in labels
        ):
            continue
        for status in pod.get("status", {}).get("containerStatuses", []):
            if status.get("containerID"):
                result[pod["metadata"]["name"] + "/" + status["name"]] = {
                    "pod": pod["metadata"]["name"],
                    "name": status["name"],
                    "uid": pod["metadata"]["uid"],
                    "node": pod["spec"]["nodeName"],
                    "container": status["containerID"].removeprefix("containerd://"),
                    "ready": status["ready"],
                    "restarts": status["restartCount"],
                }
    return result


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
                    "replicas": list(map(int, fields["Replicas"].split(","))),
                    "isr": [
                        int(value)
                        for value in fields.get("Isr", "").split(",")
                        if value
                    ],
                }
            )
    if not rows or len({(r["topic"], r["partition"]) for r in rows}) != len(rows):
        raise ValueError("missing or duplicated native partition metadata")
    return rows


def embedded_json(text):
    for line in text.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("native command did not return JSON")


def cli(side, tool, *arguments, pod=None):
    candidates = [
        state
        for state in identities().values()
        if state["pod"].startswith(side + "-" + side + "-brokers-")
        and state["ready"]
        and state["name"] == "kafka"
    ]
    if pod is not None:
        candidates = [state for state in candidates if state["pod"] == pod]
    if not candidates:
        raise RuntimeError("no healthy " + side + " broker for native inspection")
    text = lab.call(
        lab.KUBE
        + [
            "exec",
            sorted(candidates, key=lambda p: p["pod"])[-1]["pod"],
            "-c",
            "kafka",
            "--",
            "/opt/kafka/bin/" + tool,
            "--bootstrap-server",
            side + "-kafka-bootstrap:9092",
            *arguments,
        ]
    )

    lab.save(
        lab.DATA / "native-commands" / (uuid.uuid4().hex + ".json"),
        {
            "time": time.time(),
            "side": side,
            "tool": tool,
            "arguments": arguments,
            "stdout": text,
        },
    )
    return text


def connect():
    candidates = [
        state
        for state in identities().values()
        if state["pod"].startswith("replication-mirrormaker2-") and state["ready"]
    ]
    if not candidates:
        raise RuntimeError("no healthy MirrorMaker worker for native task inspection")
    worker = sorted(candidates, key=lambda state: state["pod"])[0]
    # The operator restricts the REST listener; use its existing loopback endpoint.
    text = lab.call(
        lab.KUBE
        + [
            "exec",
            worker["pod"],
            "-c",
            worker["name"],
            "--",
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "10",
            "http://localhost:8083/connectors?expand=status",
        ],
        timeout=20,
    )
    return json.loads(text)


def native(topics):
    ids = identities()
    brokers = [s for s in ids.values() if s["name"] == "kafka"]
    if len(brokers) != 12 or not all(s["ready"] for s in ids.values()):
        return {"time": time.time(), "identities": ids, "transitional": True}
    result = {"time": time.time(), "identities": ids, "sides": {}}
    for side in ("east", "west"):
        result["sides"][side] = {
            "partitions": partitions(cli(side, "kafka-topics.sh", "--describe")),
            "quorum": cli(side, "kafka-metadata-quorum.sh", "describe", "--status"),
            "logs": embedded_json(
                cli(side, "kafka-log-dirs.sh", "--describe", "--broker-list", "3,4,5")
            ),
        }
    result["connect"] = connect()
    return result


def healthy(snapshot, topics):
    if snapshot.get("transitional"):
        return False
    ids = snapshot["identities"]
    workers = {lab.CLUSTER + suffix for suffix in ("-worker", "-worker2", "-worker3")}
    for alias in ("east", "west"):
        for role, members in (("controllers", range(3)), ("brokers", range(3, 6))):
            keys = [f"{alias}-{alias}-{role}-{member}/kafka" for member in members]
            if any(key not in ids or not ids[key]["ready"] for key in keys):
                return False
            if {ids[key]["node"] for key in keys} != workers:
                return False
    mm2 = [
        state
        for state in ids.values()
        if state["pod"].startswith("replication-mirrormaker2-")
    ]
    if len(mm2) != 2 or not all(state["ready"] for state in ids.values()):
        return False
    cluster_ids = [
        re.search(r"^ClusterId:\s*(\S+)$", side["quorum"], re.MULTILINE)
        for side in snapshot["sides"].values()
    ]
    if (
        any(identifier is None for identifier in cluster_ids)
        or len({identifier[1] for identifier in cluster_ids}) != 2
    ):
        return False
    for side in snapshot["sides"].values():
        rows = [r for r in side["partitions"] if r["topic"] in topics]
        if {(r["topic"], r["partition"]) for r in rows} != {
            (t, p) for t in topics for p in range(6)
        }:
            return False
        if any(
            set(r["replicas"]) != {3, 4, 5}
            or set(r["isr"]) != {3, 4, 5}
            or r["leader"] not in {3, 4, 5}
            for r in side["partitions"]
        ):
            return False
        logs = side["logs"]["brokers"]
        if {b["broker"] for b in logs} != {3, 4, 5}:
            return False
        expected_native = {
            row["topic"] + "-" + str(row["partition"]) for row in side["partitions"]
        }
        for broker in logs:
            found = set()
            for directory in broker["logDirs"]:
                if directory["error"] is not None:
                    return False
                for part in directory["partitions"]:
                    if part["offsetLag"] != 0 or part.get("isFuture", False):
                        return False
                    found.add(part["partition"])
            if found != expected_native:
                return False
        voters = re.search(r"^CurrentVoters:\s*(.+)$", side["quorum"], re.MULTILINE)
        lag = re.search(r"^MaxFollowerLag:\s*(\d+)$", side["quorum"], re.MULTILINE)
        if (
            not voters
            or not lag
            or int(lag[1]) != 0
            or {v["id"] for v in json.loads(voters[1])} != {0, 1, 2}
            or not re.search(r"^LeaderId:\s*[012]\b", side["quorum"], re.MULTILINE)
        ):
            return False
    source = (
        snapshot["connect"]
        .get("east->west.MirrorSourceConnector", {})
        .get("status", {})
    )
    checkpoints = (
        snapshot["connect"]
        .get("east->west.MirrorCheckpointConnector", {})
        .get("status", {})
    )
    return (
        source.get("connector", {}).get("state") == "RUNNING"
        and len(source.get("tasks", [])) == 3
        and all(t["state"] == "RUNNING" for t in source["tasks"])
        and checkpoints.get("connector", {}).get("state") == "RUNNING"
        and all(t["state"] == "RUNNING" for t in checkpoints.get("tasks", []))
    )


def data_volume(inspection, uid):
    mounts = [
        mount
        for mount in inspection["info"]["runtimeSpec"]["mounts"]
        if mount["destination"] == "/var/lib/kafka/data"
    ]
    prefix = "/var/lib/kubelet/pods/" + uid + "/volumes/kubernetes.io~empty-dir/"
    if (
        len(mounts) != 1
        or mounts[0]["type"] != "bind"
        or not mounts[0]["source"].startswith(prefix)
        or "rw" not in mounts[0]["options"]
    ):
        raise RuntimeError("broker data is not a verified writable, UID-owned EmptyDir")
    return mounts[0]["source"]


def empty_history(case, record=False):
    if len(case) != 32 or any(char not in "0123456789abcdef" for char in case):
        raise ValueError("invalid empty-history case ID")
    lab.owner()
    root = lab.DATA / "cases" / case
    experiment = json.loads((root / "experiment.json").read_text())
    if experiment["id"] != case or not (root / "failure.json").exists():
        raise RuntimeError("only a failed setup can be sealed as never started")
    if not record:
        baseline = json.loads((root / "no-workload-started.json").read_text())
        if (
            baseline["case"] != case
            or baseline["original_status"] != "failed"
            or baseline["acknowledged"] != 0
        ):
            raise RuntimeError("invalid original failed-state receipt")
    jobs = lab.kube("get", "jobs", "-l", "lab.example/case=" + case, "-o", "json")[
        "items"
    ]
    if jobs:
        raise RuntimeError("cannot establish never-started status: workload Jobs exist")
    for side in ("east", "west"):
        if any(
            (root / side / name).exists()
            for name in (
                "start-producer",
                "start-reader",
                "producer.jsonl.gz",
                "reader.jsonl.gz",
            )
        ):
            raise RuntimeError(
                "cannot establish never-started status: workload state exists"
            )
    offsets = {}
    for side in ("east", "west"):
        offsets[side] = {}
        for boundary in ("earliest", "latest"):
            text = cli(
                side,
                "kafka-get-offsets.sh",
                "--topic",
                experiment["topic"],
                "--time",
                boundary,
            )
            rows = [line.split(":") for line in text.splitlines()]
            if (
                len(rows) != 6
                or {int(row[1]) for row in rows} != set(range(6))
                or any(
                    len(row) != 3 or row[0] != experiment["topic"] or int(row[2]) != 0
                    for row in rows
                )
            ):
                raise RuntimeError("failed setup is not provably empty on " + side)
            offsets[side][boundary] = text
    proof = {
        "case": case,
        "original_status": "failed",
        "acknowledged": 0,
        "time": time.time(),
        "no_workload_jobs": True,
        "no_start_gates_or_journals": True,
        "native_offsets": offsets,
    }
    if record:
        lab.save(root / "no-workload-started.json", proof)
    return proof


def recovered_failure(root):
    receipt_path = root / "post-crash-reconciliation.json"
    if not (root / "failure.json").exists() or not receipt_path.exists():
        return False
    receipt = json.loads(receipt_path.read_text())
    recovery = (lab.DATA / receipt["recovery"]).resolve()
    if not recovery.is_relative_to((lab.DATA / "recoveries").resolve()):
        raise RuntimeError("recovery receipt points outside owned recovery evidence")
    result = json.loads((recovery / "result.json").read_text())
    owner = json.loads((lab.STATE / "replication-owner.json").read_text())["owner"]
    if (
        receipt["original_status"] != "failed"
        or result["owner"] != owner
        or result["proofs"][root.name] != receipt["proofs"]
    ):
        raise RuntimeError(
            "failed-history recovery receipt does not match its sealed proof"
        )
    digest = hashlib.sha256(
        (root / "east" / "producer.jsonl.gz").read_bytes()
    ).hexdigest()
    if digest != receipt["producer_witness_sha256"]:
        raise RuntimeError("recovered producer witness changed")
    for side in ("east", "west"):
        proof = receipt["proofs"][side]["result"]
        if (
            proof["strict_verification"] is not True
            or proof["acknowledged_loss"] != 0
            or proof["corruption"] != 0
            or proof["observed_acknowledged"] != proof["acknowledged"]
            or proof["run_id"] != root.name
            or proof["mirrored_offset_policy"] != (side == "west")
        ):
            raise RuntimeError(
                "recovery did not establish a strict preserved-data baseline"
            )
    return True


class Experiment:
    def __init__(self, operation):
        if operation not in {
            "correctness",
            "mm2-worker-crash",
            "west-broker-crash",
            "west-empty-disk",
        }:
            raise ValueError("operation is not qualified: " + operation)
        if (
            os.environ.get("MM2_REPLICATION_POLICY", "identity") != "identity"
            or os.environ.get("MM2_REPLICAS", "2") != "2"
            or os.environ.get("MM2_TASKS_MAX", "3") != "3"
        ):
            raise ValueError(
                "live fault qualification requires identity policy, two workers and three source tasks"
            )
        self.id = uuid.uuid4().hex
        self.root = lab.DATA / "cases" / self.id
        self.root.mkdir(parents=True)
        self.operation = operation
        self.phases = []
        self.jobs = []
        self.preferred_leader = None
        self.allowed_replacements = set()
        self.original_identities = None
        self.recorder = None
        count = int(os.environ.get("COUNT", "2000000"))
        if not 1_000_000 <= count <= 2_000_000:
            raise ValueError("COUNT must be between one and two million")
        self.topic = "orders-" + str(time.time_ns())
        for side in ("east", "west"):
            root = self.root / side
            root.mkdir()
            plan = {
                "format_version": 1,
                "run_id": self.id,
                "topic": self.topic,
                "bootstrap_servers": side + "-kafka-bootstrap:9092",
                "minimum_messages": count,
                "warmup_messages": 100000,
                "live_rate": int(os.environ.get("LIVE_RATE", "1000")),
                "burst_rate": 0,
                "duration_seconds": 1800,
                "delivery_timeout_ms": 300000,
                "partitions": 6,
                "group_id": "live-" + side + "-" + self.id,
                "mirrored": side == "west",
            }
            lab.save(root / "case.json", plan)
        lab.save(
            self.root / "experiment.json",
            {
                "id": self.id,
                "operation": operation,
                "topic": self.topic,
                "time": time.time(),
            },
        )
        self.phase("setup")

    def phase(self, phase, **details):
        marker = {"time": time.time(), "phase": phase, **details}
        self.phases.append(marker)
        lab.save(self.root / "phases.json", self.phases, replace=True)
        print(json.dumps({"case": self.id, **marker}), flush=True)

    def observations(self):
        if self.recorder is not None:
            self.recorder.check()
        result = {"time": time.time()}
        for side in ("east", "west"):
            root = self.root / side
            for actor in ("reader", "producer") if side == "east" else ("reader",):
                if (root / "failure.json").exists():
                    raise RuntimeError(
                        "live application failure: "
                        + (root / "failure.json").read_text()
                    )
                path = root / (actor + "-progress.json")
                if path.exists():
                    result[side + "-" + actor] = json.loads(path.read_text())
        with (self.root / "observations.jsonl").open("a") as stream:
            stream.write(json.dumps(result) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for name in self.jobs:
            state = lab.kube("get", "job", name, "-o", "json").get("status", {})
            if state.get("failed"):
                lab.completed(name, self.root)
        return result

    def alive(self):
        sample = self.observations()
        if (self.root / "east" / "producer.json").exists():
            raise RuntimeError("producer exited before repair completed")
        for role in ("east-producer", "east-reader", "west-reader"):
            if role not in sample or time.time() - sample[role]["time"] > 15:
                raise RuntimeError("live application progress is unavailable: " + role)
        return sample

    def wait_native(self, timeout=600):
        topics = {
            json.loads(p.read_text())["topic"]
            for p in (lab.DATA / "cases").glob("*/experiment.json")
        }

        def check():
            if self.jobs:
                self.alive()
            snapshot = native(topics)
            lab.save(self.root / ("native-" + str(time.time_ns()) + ".json"), snapshot)
            if not healthy(snapshot, topics):
                return None
            if self.preferred_leader is not None and any(
                row["leader"] != self.preferred_leader
                for row in snapshot["sides"]["west"]["partitions"]
                if row["topic"] == self.topic
            ):
                return None
            return snapshot

        return lab.wait(check, "native replicas, log replay and Connect tasks", timeout)

    def native_file(self, tool, flag, document, *arguments):
        candidates = [
            state
            for state in identities().values()
            if state["pod"].startswith("west-west-brokers-") and state["ready"]
        ]
        pod = sorted(candidates, key=lambda state: state["pod"])[-1]["pod"]
        identifier = uuid.uuid4().hex
        path = "/tmp/mirror-recovery-" + identifier + ".json"
        lab.save(self.root / ("native-plan-" + identifier + ".json"), document)
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
                "write-plan",
                path,
            ],
            stdin=json.dumps(document),
        )
        try:
            return cli("west", tool, flag, path, *arguments, pod=pod)
        finally:
            lab.call(lab.KUBE + ["exec", pod, "-c", "kafka", "--", "rm", path])

    def serve_from_repaired_broker(self, broker):
        self.phase("promote-repaired-copy", broker=broker)
        self.native_file(
            "kafka-reassign-partitions.sh",
            "--reassignment-json-file",
            {
                "version": 1,
                "partitions": [
                    {
                        "topic": self.topic,
                        "partition": partition,
                        "replicas": [
                            broker,
                            *[node for node in (3, 4, 5) if node != broker],
                        ],
                    }
                    for partition in range(6)
                ],
            },
            "--execute",
        )
        snapshot = self.wait_native()
        rows = [
            row
            for row in snapshot["sides"]["west"]["partitions"]
            if row["topic"] == self.topic
        ]
        if len(rows) != 6 or any(row["replicas"][0] != broker for row in rows):
            raise RuntimeError("native preferred replica order did not converge")
        self.native_file(
            "kafka-leader-election.sh",
            "--path-to-json-file",
            {
                "partitions": [
                    {"topic": self.topic, "partition": partition}
                    for partition in range(6)
                ]
            },
            "--election-type",
            "preferred",
        )
        self.preferred_leader = broker
        self.wait_native()
        self.phase("repaired-copy-serving", broker=broker)

    def crash(self, target, empty_disk=False):
        before = identities()
        old = before[target]
        self.allowed_replacements.add(target)
        if self.operation == "mm2-worker-crash":
            tasks = connect()["east->west.MirrorSourceConnector"]["status"]["tasks"]
            if not any(
                task["state"] == "RUNNING"
                and task["worker_id"].split(".")[0] == old["pod"]
                for task in tasks
            ):
                raise RuntimeError(
                    "selected worker no longer owns a running source task"
                )
        else:
            leaders = partitions(
                cli("west", "kafka-topics.sh", "--describe", "--topic", self.topic)
            )
            leader = next(row["leader"] for row in leaders if row["partition"] == 0)
            if old["pod"] != f"west-west-brokers-{leader}":
                raise RuntimeError(
                    "selected broker is no longer the partition-zero leader"
                )
        node = lab.node(old["node"])
        inspection = json.loads(
            lab.call(
                ["docker", "exec", node["Id"], "crictl", "inspect", old["container"]]
            )
        )
        status = inspection["status"]
        if (
            status["id"] != old["container"]
            or status["state"] != "CONTAINER_RUNNING"
            or status["labels"]["io.kubernetes.pod.uid"] != old["uid"]
        ):
            raise RuntimeError("native crash target identity mismatch")
        pid = int(inspection["info"]["pid"])
        if pid <= 1 or old["container"] not in lab.call(
            ["docker", "exec", node["Id"], "cat", f"/proc/{pid}/cgroup"]
        ):
            raise RuntimeError("unsafe or incorrectly fenced target PID")
        lab.save(
            self.root / "fault-before.json", {"identities": before, "cri": inspection}
        )
        old_volume = (
            data_volume(inspection, old["uid"]) if old["name"] == "kafka" else None
        )
        paused = False
        try:
            if empty_disk:
                pod = lab.kube("get", "pod", old["pod"], "-o", "json")
                volume = old_volume.rsplit("/", 1)[1]
                declared = [v for v in pod["spec"]["volumes"] if v["name"] == volume]
                if (
                    pod["metadata"]["uid"] != old["uid"]
                    or len(declared) != 1
                    or "emptyDir" not in declared[0]
                    or declared[0]["emptyDir"].get("medium", "") != ""
                ):
                    raise RuntimeError(
                        "empty-disk test requires a disk-backed EmptyDir"
                    )
                # Prevent kubelet from restarting the old writer between fencing and
                # marking its Pod for normal volume cleanup. Other containers keep running.
                paused = True
                lab.call(["docker", "exec", node["Id"], "systemctl", "stop", "kubelet"])
                if (
                    lab.call(
                        [
                            "docker",
                            "exec",
                            node["Id"],
                            "systemctl",
                            "show",
                            "kubelet",
                            "--property=ActiveState",
                            "--value",
                        ]
                    ).strip()
                    != "inactive"
                ):
                    raise RuntimeError("owned worker kubelet did not stop")
                self.phase("kubelet-paused-for-fencing", node=old["node"])
            self.phase("fault", target=target, container=old["container"], pid=pid)
            lab.call(["docker", "exec", node["Id"], "kill", "-KILL", str(pid)])

            def fenced():
                records = json.loads(
                    lab.call(
                        [
                            "docker",
                            "exec",
                            node["Id"],
                            "crictl",
                            "ps",
                            "-a",
                            "-o",
                            "json",
                        ]
                    )
                )
                lab.save(
                    self.root / ("fence-" + str(time.time_ns()) + ".json"), records
                )
                matches = [
                    r for r in records["containers"] if r["id"] == old["container"]
                ]
                return not matches or matches[0]["state"] == "CONTAINER_EXITED"

            lab.wait(fenced, "positive old process exit", 60)
            self.phase("process-fenced")
            if empty_disk:
                records = json.loads(
                    lab.call(
                        [
                            "docker",
                            "exec",
                            node["Id"],
                            "crictl",
                            "ps",
                            "-a",
                            "-o",
                            "json",
                        ]
                    )
                )
                if any(
                    r.get("labels", {}).get("io.kubernetes.pod.uid") == old["uid"]
                    and r["state"] == "CONTAINER_RUNNING"
                    for r in records["containers"]
                ):
                    raise RuntimeError(
                        "an old writer remains alive before volume removal"
                    )
                lab.save(self.root / "fenced-before-volume-replacement.json", records)
                lab.call(lab.KUBE + ["delete", "pod", old["pod"], "--wait=false"])
                pods = lab.kube("get", "pods", "-o", "json")
                existing = [
                    p for p in pods["items"] if p["metadata"]["uid"] == old["uid"]
                ]
                if existing and not existing[0]["metadata"].get("deletionTimestamp"):
                    raise RuntimeError(
                        "old Pod was not marked for normal kubelet cleanup"
                    )
                lab.save(
                    self.root / "volume-replacement-authorized.json",
                    {"pods": pods, "old_volume": old_volume, "old_uid": old["uid"]},
                )
        finally:
            if paused:
                lab.call(
                    ["docker", "exec", node["Id"], "systemctl", "start", "kubelet"]
                )
                if (
                    lab.call(
                        [
                            "docker",
                            "exec",
                            node["Id"],
                            "systemctl",
                            "show",
                            "kubelet",
                            "--property=ActiveState",
                            "--value",
                        ]
                    ).strip()
                    != "active"
                ):
                    raise RuntimeError("owned worker kubelet did not resume")
                self.phase("kubelet-resumed", node=old["node"])

        def restarted():
            self.alive()
            current = identities()
            if (
                target not in current
                or not current[target]["ready"]
                or current[target]["container"] == old["container"]
            ):
                return None
            if empty_disk and current[target]["uid"] == old["uid"]:
                return None
            if not empty_disk and current[target]["uid"] != old["uid"]:
                raise RuntimeError(
                    "retained-volume crash unexpectedly replaced Pod storage"
                )
            for key, state in before.items():
                if key != target and (
                    key not in current
                    or current[key]["container"] != state["container"]
                ):
                    raise RuntimeError("unrelated survivor restarted: " + key)
            return current

        after = lab.wait(restarted, "replacement process readiness", 300)
        self.phase(
            "replacement-ready",
            pod_uid=after[target]["uid"],
            container=after[target]["container"],
        )
        if empty_disk:
            records = json.loads(
                lab.call(
                    ["docker", "exec", node["Id"], "crictl", "ps", "-a", "-o", "json"]
                )
            )
            lab.save(self.root / "old-pod-fenced.json", records)
            if any(
                r.get("labels", {}).get("io.kubernetes.pod.uid") == old["uid"]
                and r["state"] == "CONTAINER_RUNNING"
                for r in records["containers"]
            ):
                raise RuntimeError(
                    "an old Pod process remains alive after storage replacement"
                )
        if old_volume is not None:
            replacement = json.loads(
                lab.call(
                    [
                        "docker",
                        "exec",
                        lab.node(after[target]["node"])["Id"],
                        "crictl",
                        "inspect",
                        after[target]["container"],
                    ]
                )
            )
            new_volume = data_volume(replacement, after[target]["uid"])
            if (new_volume != old_volume) != empty_disk:
                raise RuntimeError(
                    "retained versus replaced data-volume identity is incorrect"
                )
            lab.save(
                self.root / "data-volume-after.json",
                {"old": old_volume, "new": new_volume, "cri": replacement},
            )
        if empty_disk:

            def old_directory_removed():
                entries = lab.call(
                    ["docker", "exec", node["Id"], "ls", "-1", "/var/lib/kubelet/pods"]
                ).splitlines()
                lab.save(
                    self.root / ("pod-directories-" + str(time.time_ns()) + ".json"),
                    entries,
                )
                return old["uid"] not in entries

            lab.wait(
                old_directory_removed,
                "native removal of old UID-owned EmptyDir storage",
                180,
            )
            self.phase("old-storage-removed", old_uid=old["uid"])
        lab.save(self.root / "fault-after.json", after)
        self.phase(
            "process-restarted",
            pod_uid=after[target]["uid"],
            container=after[target]["container"],
        )

    def verify(self, case, side):
        root = lab.DATA / "cases" / case / side
        output = root / "verifications" / uuid.uuid4().hex
        name = lab.job(case, side, "verify", output)
        lab.wait(
            lambda: lab.completed(name, output), "full durable-witness replay", 900
        )
        result = json.loads((output / "verification.json").read_text())
        if (
            result.get("strict_verification") is not True
            or result["acknowledged_loss"] != 0
            or result["corruption"] != 0
            or result["observed_acknowledged"] != result["acknowledged"]
            or result["mirrored_offset_policy"] != (side == "west")
        ):
            raise RuntimeError("strict body/partition/ACK reconciliation failed")
        return {"path": str(output.relative_to(lab.DATA)), "result": result}

    def run(self):
        lab.owner()
        self.recorder = resources.Recorder(self.root)
        self.recorder.start()
        topic = {
            "apiVersion": "kafka.strimzi.io/v1",
            "kind": "KafkaTopic",
            "metadata": {"name": self.topic, "labels": {"strimzi.io/cluster": "east"}},
            "spec": {
                "partitions": 6,
                "replicas": 3,
                "config": {"min.insync.replicas": 2},
            },
        }
        lab.call(lab.KUBE + ["create", "-f", "-"], stdin=json.dumps(topic))
        lab.call(
            lab.KUBE
            + [
                "wait",
                "kafkatopic/" + self.topic,
                "--for=condition=Ready",
                "--timeout=180s",
            ],
            timeout=200,
        )
        lab.call(["make", "mirror"], timeout=700)
        initial = self.wait_native()
        self.original_identities = initial["identities"]
        lab.save(self.root / "initial-pods.json", lab.kube("get", "pods", "-o", "json"))
        lab.save(
            self.root / "initial-nodepools.json",
            lab.kube("get", "kafkanodepools", "-o", "json"),
        )
        for side in ("east", "west"):
            self.jobs.append(lab.job(self.id, side, "reader"))
        self.jobs.append(lab.job(self.id, "east", "producer"))

        def warmed():
            sample = self.observations()
            return sample.get("east-producer", {}).get(
                "acknowledged", 0
            ) >= 100000 and all(
                sample.get(side + "-reader", {}).get("unique_messages", 0) >= 100000
                for side in ("east", "west")
            )

        lab.wait(warmed, "independent source and target reader progress", 300)
        self.phase("baseline")
        baseline = self.alive()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.alive()
            time.sleep(1)
        fresh = self.wait_native()

        def advanced(reference):
            sample = self.alive()
            roles = (
                ("east-producer", "acknowledged"),
                ("east-reader", "unique_messages"),
                ("west-reader", "unique_messages"),
            )
            return (
                sample
                if all(sample[role][key] > reference[role][key] for role, key in roles)
                else None
            )

        before = lab.wait(lambda: advanced(baseline), "live progress before fault", 180)
        if self.operation == "mm2-worker-crash":
            tasks = fresh["connect"]["east->west.MirrorSourceConnector"]["status"][
                "tasks"
            ]
            pod = tasks[0]["worker_id"].split(".")[0]
            candidates = [
                key for key, state in fresh["identities"].items() if state["pod"] == pod
            ]
            if len(candidates) != 1:
                raise RuntimeError("source-task worker ownership is ambiguous")
            self.crash(candidates[0])
        elif self.operation in {"west-broker-crash", "west-empty-disk"}:
            row = next(
                r
                for r in fresh["sides"]["west"]["partitions"]
                if r["topic"] == self.topic and r["partition"] == 0
            )
            target = f"west-west-brokers-{row['leader']}/kafka"
            self.crash(target, self.operation == "west-empty-disk")
        elif self.operation != "correctness":
            raise ValueError("unknown operation")
        self.phase("native-recovery")
        self.wait_native(900)
        if self.operation in {"west-broker-crash", "west-empty-disk"}:
            self.serve_from_repaired_broker(int(target.split("/")[0].rsplit("-", 1)[1]))
        self.phase("recovered-live")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.alive()
            time.sleep(1)
        lab.wait(lambda: advanced(before), "live progress after native recovery", 180)
        self.phase("finish-load")
        (self.root / "east" / "finish-producer").touch(exist_ok=False)
        producer = self.jobs[-1]
        lab.wait(lambda: lab.completed(producer, self.root), "producer completion", 600)
        result = json.loads((self.root / "east" / "producer.json").read_text())
        if (
            not result["stopped_by_controller"]
            or result["ambiguous"]
            or result["rejected"]
        ):
            raise RuntimeError(
                "producer has ambiguous/rejected outcomes; retained for diagnosis"
            )
        acknowledged = result["acknowledged"]
        lab.wait(
            lambda: all(
                self.observations().get(side + "-reader", {}).get("unique_messages", 0)
                >= acknowledged
                for side in ("east", "west")
            ),
            "both live readers caught up",
            600,
        )
        os.link(
            self.root / "east" / "producer.jsonl.gz",
            self.root / "west" / "producer.jsonl.gz",
        )
        for side in ("east", "west"):
            (self.root / side / "stop-reader").touch(exist_ok=False)
        for job in self.jobs[:-1]:
            lab.wait(
                lambda name=job: lab.completed(name, self.root),
                "reader completion",
                180,
            )
        self.jobs = []
        self.wait_native()
        self.phase("reconcile")
        proofs = {}
        for case in sorted((lab.DATA / "cases").iterdir()):
            if not (case / "experiment.json").exists():
                continue
            if case.name != self.id and (case / "no-workload-started.json").exists():
                proofs[case.name] = empty_history(case.name)
                continue
            if (
                case.name != self.id
                and not (case / "result.json").exists()
                and not recovered_failure(case)
            ):
                raise RuntimeError(
                    "earlier incomplete/failed history requires diagnosis: " + case.name
                )
            proofs[case.name] = {
                side: self.verify(case.name, side) for side in ("east", "west")
            }
            if (case / "failure.json").exists():
                proofs[case.name]["original_status"] = "failed"
        reports = {
            side: report.reconcile(
                self.root / side,
                json.loads((self.root / side / "case.json").read_text()),
                self.phases,
            )
            for side in ("east", "west")
        }
        final = self.wait_native()
        if any(
            key not in final["identities"]
            or final["identities"][key]["container"] != old["container"]
            for key, old in self.original_identities.items()
            if key not in self.allowed_replacements
        ):
            raise RuntimeError(
                "an unrelated survivor restarted during the experiment or reconciliation"
            )
        self.recorder.stop()
        resource_report = resources.summarize(self.root / "resources.jsonl.gz")
        self.recorder = None
        self.phase("complete")
        lab.save(
            self.root / "result.json",
            {
                "passed": True,
                "operation": self.operation,
                "case": self.id,
                "acknowledged": acknowledged,
                "proofs": proofs,
                "reports": reports,
                "resources": resource_report,
                "phases": self.phases,
            },
        )
        print(
            json.dumps({"passed": True, "case": self.id, "acknowledged": acknowledged}),
            flush=True,
        )

    def failed(self, error):
        if self.recorder is not None:
            try:
                self.recorder.stop()
            except RuntimeError as recorder_error:
                lab.save(
                    self.root / "resource-recorder-failure.json",
                    {"error": str(recorder_error)},
                )
            self.recorder = None
        self.phase("failed", error=str(error))
        for side in ("east", "west"):
            for marker in (
                ("abort-producer", "abort-reader")
                if side == "east"
                else ("abort-reader",)
            ):
                (self.root / side / marker).touch(exist_ok=True)
        lab.save(
            self.root / "failure.json",
            {
                "passed": False,
                "operation": self.operation,
                "error": str(error),
                "time": time.time(),
            },
        )
        for name in self.jobs:
            try:
                lab.wait(
                    lambda: lab.completed(name, self.root),
                    "aborted workload completion",
                    90,
                )
            except (RuntimeError, FileExistsError) as problem:
                lab.save(
                    self.root / ("abort-" + name + ".json"), {"error": str(problem)}
                )


def readback(plans, *, second_run=None):
    """Replay deterministic cutover fixtures with fresh, example-owned groups."""
    receipt = lab.owner()
    root = lab.DATA / "lifecycle" / ("readback-" + uuid.uuid4().hex)
    lab.save(
        root / "plan.json",
        {"owner": receipt, "fixtures": plans, "second_west_run": second_run},
    )
    template = json.loads(
        lab.call(
            lab.KUBE
            + [
                "create",
                "--dry-run=client",
                "--validate=false",
                "-f",
                str(lab.ROOT / "jobs/consumer.yaml"),
                "-o",
                "json",
            ]
        )
    )
    for side, topic, run, count in plans:
        if side not in {"east", "west"} or not run or not 1 <= count <= 2_000_000:
            raise ValueError("invalid retained fixture")
        name = "retained-" + side + "-" + uuid.uuid4().hex[:16]
        job = copy.deepcopy(template)
        job["metadata"]["name"] = name
        job["metadata"]["labels"]["app"] = "kafka-retained-consumer"
        pod = job["spec"]["template"]
        pod["metadata"]["labels"]["app"] = "kafka-retained-consumer"
        values = {
            "BOOTSTRAP_SERVERS": side + "-kafka-bootstrap:9092",
            "TOPIC": topic,
            "GROUP_ID": name,
            "RUN_ID": run,
            "EXPECTED_COUNT": str(count),
            "TIMEOUT_SECONDS": "1800",
        }
        if side == "west" and second_run is not None:
            values["SECOND_RUN_ID"] = second_run
        pod["spec"]["containers"][0]["env"] = [
            {"name": key, "value": value} for key, value in values.items()
        ]
        lab.save(root / (name + "-expected.json"), job)
        lab.call(lab.KUBE + ["create", "-f", "-"], stdin=json.dumps(job))
        try:
            lab.wait(lambda: lab.completed(name, root), "retained data readback", 1850)
        except Exception:
            lab.save(
                root / (name + "-pods.json"), lab.kube("get", "pods", "-o", "json")
            )
            raise
        lab.save(
            root / (name + "-pods.json"),
            lab.kube("get", "pods", "-l", "job-name=" + name, "-o", "json"),
        )
    lab.save(
        root / "result.json",
        {"passed": True, "fixtures": plans, "second_west_run": second_run},
    )
    print(json.dumps({"retained_readback": str(root), "passed": True}), flush=True)


def teardown():
    lab.owner()
    cases = list((lab.DATA / "cases").glob("*/experiment.json"))
    if cases:
        audit = Experiment.__new__(Experiment)
        audit.root = lab.DATA / "cleanup-verification" / uuid.uuid4().hex
        audit.root.mkdir(parents=True)
        for path in cases:
            if (path.parent / "no-workload-started.json").exists():
                empty_history(path.parent.name)
                continue
            if (
                not (path.parent / "result.json").exists()
                or (path.parent / "failure.json").exists()
            ) and not recovered_failure(path.parent):
                raise RuntimeError(
                    "refusing teardown with failed/unqualified history: "
                    + path.parent.name
                )
            for side in ("east", "west"):
                audit.verify(path.parent.name, side)
    for arguments in [
        ["delete", "kafkamirrormaker2", "--all", "--wait=true", "--timeout=180s"],
        ["delete", "kafkatopics", "--all", "--wait=true", "--timeout=180s"],
        ["delete", "kafka", "--all", "--wait=true", "--timeout=300s"],
        ["delete", "kafkanodepools", "--all", "--wait=true", "--timeout=180s"],
    ]:
        lab.call(lab.KUBE + arguments, timeout=330)
    lab.call(
        [
            "helm",
            "--kube-context",
            lab.CONTEXT,
            "uninstall",
            "strimzi-operator",
            "--namespace",
            "kafka",
            "--wait",
        ],
        timeout=330,
    )
    lab.call(
        lab.KUBE + ["delete", "namespace", "kafka", "--wait=true", "--timeout=180s"],
        timeout=200,
    )


def reconcile_history():
    receipt = lab.owner()
    root = lab.DATA / "recoveries" / uuid.uuid4().hex
    root.mkdir(parents=True)
    lab.save(root / "pods-before.json", lab.kube("get", "pods", "-o", "json"))
    cases = sorted((lab.DATA / "cases").glob("*/experiment.json"))
    topics = {json.loads(path.read_text())["topic"] for path in cases}
    snapshot = native(topics)
    lab.save(root / "native-before.json", snapshot)
    if not healthy(snapshot, topics):
        raise RuntimeError(
            "native repair must complete before post-crash reconciliation"
        )
    audit = Experiment.__new__(Experiment)
    audit.root = root
    proofs = {}
    total = 0
    for path in cases:
        case = path.parent.name
        if (path.parent / "no-workload-started.json").exists():
            proofs[case] = empty_history(case)
            continue
        producer = json.loads((path.parent / "east" / "producer.json").read_text())
        if producer["ambiguous"] or producer["rejected"] or producer["aborted"]:
            raise RuntimeError("history has unresolved producer outcomes: " + case)
        proofs[case] = {side: audit.verify(case, side) for side in ("east", "west")}
        if any(
            proof["result"]["acknowledged"] != producer["acknowledged"]
            for proof in proofs[case].values()
        ):
            raise RuntimeError(
                "replay acknowledgement count differs from durable final witness"
            )
        total += producer["acknowledged"]
        print(
            json.dumps({"reconciled": case, "acknowledged": producer["acknowledged"]}),
            flush=True,
        )
    snapshot = native(topics)
    lab.save(root / "native-after.json", snapshot)
    if not healthy(snapshot, topics):
        raise RuntimeError("native redundancy changed during history readback")
    lab.save(
        root / "result.json",
        {
            "time": time.time(),
            "acknowledged": total,
            "proofs": proofs,
            "owner": receipt["owner"],
            "scope": "read-only post-crash history reconciliation; original failed experiments remain failed",
        },
    )
    for path in cases:
        if (path.parent / "failure.json").exists() and not (
            path.parent / "no-workload-started.json"
        ).exists():
            lab.save(
                path.parent / "post-crash-reconciliation.json",
                {
                    "original_status": "failed",
                    "recovery": str(root.relative_to(lab.DATA)),
                    "proofs": proofs[path.parent.name],
                    "producer_witness_sha256": hashlib.sha256(
                        (path.parent / "east" / "producer.jsonl.gz").read_bytes()
                    ).hexdigest(),
                },
                replace=True,
            )
    print(json.dumps({"recovery": str(root), "acknowledged": total}), flush=True)


def main():
    lab.STATE.mkdir(exist_ok=True)
    action = sys.argv[1]
    with (
        (lab.STATE / "replication.lock").open("a") as lock,
        (lab.STATE / "cutover.lock").open("a") as cutover_lock,
    ):
        if action != "cutover":
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action not in {"record-images", "validate-topic"}:
            fcntl.flock(cutover_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == "cutover":
            # The inner Make keeps this lock even if its Python launcher exits.
            subprocess.run(
                ["make", "test-cutover"],
                cwd=lab.ROOT,
                env=lab.ENV,
                pass_fds=(cutover_lock.fileno(),),
                check=True,
            )
        elif action == "kind-up":
            lab.kind_up()
        elif action == "kind-down":
            lab.kind_down()
        elif action == "down":
            teardown()
        elif action == "verify-retained":
            topic, run = os.environ["TOPIC"], os.environ["RUN_ID"]
            count = int(os.environ["COUNT"])
            readback(
                [(side, topic, run, count) for side in ("east", "west")],
                second_run=run + "-west",
            )
        elif action == "reconcile-history":
            reconcile_history()
        elif action == "validate-topic":
            if re.fullmatch(r"orders-[0-9]+", os.environ.get("TOPIC", "")) is None:
                raise ValueError(
                    "TOPIC must match ^orders-[0-9]+$ (the MM2 topic filter)"
                )
        elif action == "record-images":
            lab.record_images(*sys.argv[2:])
        elif action == "seal-empty-setup":
            empty_history(os.environ["CASE_ID"], record=True)
        else:
            actions = (
                [
                    "correctness",
                    "mm2-worker-crash",
                    "west-broker-crash",
                    "west-empty-disk",
                ]
                if action == "test-production"
                else [action]
            )
            for path in (lab.DATA / "cases").glob("*/experiment.json"):
                if (
                    not (path.parent / "result.json").exists()
                    and not (path.parent / "no-workload-started.json").exists()
                    and not recovered_failure(path.parent)
                ):
                    raise RuntimeError(
                        "earlier unqualified history requires diagnosis before new work: "
                        + path.parent.name
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
