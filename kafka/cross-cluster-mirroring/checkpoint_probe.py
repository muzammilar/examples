"""Check native MM2 translations with Kafka's formatter and a Rust inspection Pod."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent
CONTEXT = "kind-kafka-cross-cluster-mirroring"
KUBECTL = [
    "kubectl",
    "--kubeconfig",
    str(ROOT / "results" / "kubeconfig"),
    "--context",
    CONTEXT,
    "-n",
    "kafka",
]
CHECKPOINT = re.compile(
    r"Checkpoint\{consumerGroupId=([^,]+), topicPartition=(.+)-(\d+), "
    r"upstreamOffset=(\d+), downstreamOffset=(\d+), metadata=.*\}"
)


def save(path, body):
    with path.open("x") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def parse_checkpoints(body, group, topic):
    latest = {}
    for line in body.splitlines():
        match = CHECKPOINT.fullmatch(line)
        if not match:
            raise ValueError(f"unexpected native formatter output: {line!r}")
        observed_group, observed_topic, partition, upstream, downstream = match.groups()
        if observed_group == group and observed_topic == topic:
            partition = int(partition)
            if partition not in range(6):
                raise ValueError("unexpected checkpoint partition")
            latest[partition] = (int(upstream), int(downstream))
    if set(latest) != set(range(6)):
        raise ValueError("native checkpoints do not cover all six partitions")
    return latest


def command(directory, name, arguments, *, stdin=None, timeout=90):
    try:
        result = subprocess.run(
            KUBECTL + arguments,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        save(
            directory / f"{name}.stdout",
            stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout,
        )
        save(
            directory / f"{name}.stderr",
            stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr,
        )
        save(
            directory / f"{name}.json",
            json.dumps({"exit_code": None, "timed_out": True}) + "\n",
        )
        raise
    save(directory / f"{name}.stdout", result.stdout)
    save(directory / f"{name}.stderr", result.stderr)
    save(
        directory / f"{name}.json",
        json.dumps(
            {
                "exit_code": result.returncode,
                "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )
        + "\n",
    )
    if result.returncode:
        raise RuntimeError(f"{name} exited {result.returncode}")
    return result


def attempt(directory, args):
    sample = command(
        directory,
        "checkpoints",
        [
            "exec",
            "west-west-brokers-3",
            "-c",
            "kafka",
            "--",
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server",
            "west-kafka-bootstrap:9092",
            "--topic",
            "east.checkpoints.internal",
            "--partition",
            "0",
            "--offset",
            "earliest",
            "--timeout-ms",
            "2000",
            "--formatter",
            "org.apache.kafka.connect.mirror.formatters.CheckpointFormatter",
            "--command-property",
            "isolation.level=read_committed",
        ],
    )
    # Kafka 4.3.2 ConsoleConsumer.process catches receive errors and exits zero.
    # Its intentional idle timeout ends this sample; any other logged ERROR fails.
    errors = [line for line in sample.stderr.splitlines() if "ERROR" in line]
    if errors and (
        len(errors) != 1
        or "Error processing message, terminating consumer process" not in errors[0]
        or "org.apache.kafka.common.errors.TimeoutException" not in sample.stderr
    ):
        raise RuntimeError("native checkpoint consumer logged an unexpected error")
    checkpoints = parse_checkpoints(sample.stdout, args.group, args.target_topic)
    encoded = ",".join(
        f"{p}:{up}:{down}" for p, (up, down) in sorted(checkpoints.items())
    )
    save(directory / "translated.json", json.dumps(checkpoints, indent=2) + "\n")
    name = "checkpoint-proof-" + uuid.uuid4().hex[:16]
    environment = {
        "MODE": "checkpoint-proof",
        "SOURCE_BOOTSTRAP": "east-kafka-bootstrap:9092",
        "BOOTSTRAP_SERVERS": "west-kafka-bootstrap:9092",
        "GROUP_ID": args.group,
        "SOURCE_TOPIC": args.topic,
        "TOPIC": args.target_topic,
        "CHECKPOINT_OFFSETS": encoded,
    }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": {"app": "kafka-checkpoint-proof"}},
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 120,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": args.image,
                    "imagePullPolicy": "IfNotPresent",
                    "env": [
                        {"name": key, "value": value}
                        for key, value in environment.items()
                    ],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "64Mi"},
                        "limits": {"cpu": "1", "memory": "256Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    save(directory / "pod-input.json", json.dumps(pod, indent=2) + "\n")
    command(directory, "create", ["apply", "-f", "-"], stdin=json.dumps(pod))
    deadline = time.monotonic() + 130
    observation = 0
    while time.monotonic() < deadline:
        observation += 1
        result = subprocess.run(
            KUBECTL + ["get", "pod", name, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        save(directory / f"pod-{observation:04d}.json", result.stdout)
        observed = json.loads(result.stdout)
        phase = observed.get("status", {}).get("phase")
        if phase in {"Succeeded", "Failed"}:
            save(directory / "pod-final.json", result.stdout)
            logs = command(directory, "probe", ["logs", name])
            marker = "CHECKPOINT_PROOF partitions=6 replay_records=0 native_decoder=KafkaCheckpoint"
            if phase != "Succeeded" or marker not in logs.stdout.splitlines():
                raise RuntimeError(f"Rust checkpoint probe failed: {logs.stdout}")
            command(
                directory,
                "delete",
                ["delete", "pod", name, "--wait=true", "--timeout=30s"],
            )
            return logs.stdout
        time.sleep(2)
    raise RuntimeError(f"checkpoint Pod {name} did not finish; retained for diagnosis")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--target-topic", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--image", default="kafka-mirror-consumer:dev")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    identifier = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex
    )
    directory = ROOT / "results" / "checkpoints" / identifier
    directory.mkdir(parents=True)
    save(
        directory / "input.json",
        json.dumps(
            {
                **vars(args),
                "context": CONTEXT,
                "probe_sha256": hashlib.sha256(
                    (ROOT / "consumer/src/checkpoint.rs").read_bytes()
                ).hexdigest(),
                "scope": "idle groups, native checkpoint coverage, restored RF=3 and no application replay",
            },
            indent=2,
        )
        + "\n",
    )
    started = time.monotonic()
    count = 0
    while time.monotonic() - started < args.timeout:
        count += 1
        current = directory / f"{count:04d}"
        current.mkdir()
        try:
            output = attempt(current, args)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as error:
            save(current / "failure.txt", str(error) + "\n")
            print(f"Checkpoint not ready: {error}; evidence: {current}", flush=True)
            time.sleep(5)
            continue
        save(
            directory / "result.json",
            json.dumps(
                {
                    "passed": True,
                    "attempts": count,
                    "elapsed_seconds": time.monotonic() - started,
                    "partitions": 6,
                    "replay_records": 0,
                },
                indent=2,
            )
            + "\n",
        )
        print(output, end="", flush=True)
        print(f"Checkpoint evidence: {directory}", flush=True)
        return
    save(
        directory / "result.json",
        json.dumps(
            {
                "passed": False,
                "attempts": count,
                "elapsed_seconds": time.monotonic() - started,
            },
            indent=2,
        )
        + "\n",
    )
    raise SystemExit(f"Native checkpoint proof failed; diagnostics: {directory}")


if __name__ == "__main__":
    main()
