"""Repair existing Connect internal topics in place; never delete their offsets."""

import json
import subprocess

import lab


def main():
    path = lab.directory("connect-replication")
    before = lab.kafka("--describe")
    (path / "before.txt").write_text(before)
    partitions = []
    for line in before.splitlines():
        fields = dict(
            field.strip().split(":", 1) for field in line.split("\t") if ":" in field
        )
        if (
            fields.get("Topic", "").strip()
            not in {"pipeline-config", "pipeline-offset", "pipeline-status"}
            or "Partition" not in fields
        ):
            continue
        partition = int(fields["Partition"])
        replicas = [4, 5, 6]
        replicas = replicas[partition % 3 :] + replicas[: partition % 3]
        partitions.append(
            {
                "topic": fields["Topic"].strip(),
                "partition": partition,
                "replicas": replicas,
                "log_dirs": ["any"] * 3,
            }
        )
    if len(partitions) != 31:
        raise RuntimeError(
            "Expected existing Connect config/offset/status partition counts 1/25/5"
        )
    assignment = json.dumps({"version": 1, "partitions": partitions})
    (path / "assignment.json").write_text(assignment + "\n")
    result = subprocess.run(
        [
            *lab.COMPOSE,
            "exec",
            "-T",
            "broker1",
            "sh",
            "-c",
            "cat > /tmp/pipeline-connect-rf3.json",
        ],
        cwd=lab.ROOT,
        input=assignment,
        text=True,
        capture_output=True,
        check=True,
    )

    def reassign(action):
        return lab.command(
            "exec",
            "-T",
            "-e",
            "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m",
            "broker1",
            "/opt/kafka/bin/kafka-reassign-partitions.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--reassignment-json-file",
            "/tmp/pipeline-connect-rf3.json",
            action,
        )

    (path / "execute.txt").write_text(reassign("--execute"))
    (path / "verify.txt").write_text(reassign("--verify"))
    for topic in ("pipeline-config", "pipeline-offset", "pipeline-status"):
        (path / (topic + "-min-isr.txt")).write_text(
            lab.command(
                "exec",
                "-T",
                "-e",
                "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m",
                "broker1",
                "/opt/kafka/bin/kafka-configs.sh",
                "--bootstrap-server",
                "broker1:9092",
                "--entity-type",
                "topics",
                "--entity-name",
                topic,
                "--alter",
                "--add-config",
                "min.insync.replicas=2",
            )
        )
    # Recreate only the owned worker to read the corrected CONNECT_* variables.
    (path / "worker-recreate.txt").write_text(
        lab.command("up", "-d", "--wait", "--no-deps", "connect")
    )
    (path / "effective.properties").write_text(
        lab.command(
            "exec",
            "-T",
            "connect",
            "cat",
            "/kafka/config/connect-distributed.properties",
        )
    )
    lab.wait_for(
        lambda: (
            lab.api("/connectors/orders/status", connect=True).get("tasks") or [{}]
        )[0].get("state")
        == "RUNNING"
    )
    lab.workload("verify", path)
    lab.native(path)
    lab.save(path / "status.json", {"passed": True})
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
