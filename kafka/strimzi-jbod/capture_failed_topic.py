"""Preserve full binary Kafka records for an explicitly frozen failed history."""

import fcntl
import gzip
import hashlib
import json
import os
import re
import subprocess
import uuid

import cluster as lab
import control
import report

SEPARATOR = b"__KAFKA_RECORD_END__"


def main():
    lab.owner()
    case = os.environ["CASE_ID"]
    if not re.fullmatch("[0-9a-f]{32}", case):
        raise ValueError("invalid case identity")
    root = lab.RESULTS / "cases" / case
    baseline = json.loads((root / "known-failure-baseline.json").read_text())
    state = baseline["state"]
    if baseline["original_status"] != "failed" or state["run_id"] != case:
        raise RuntimeError("expected the explicit failed-history baseline")
    plan = json.loads((lab.WITNESS / case / "case.json").read_text())
    expected = {}
    for row in report.events(lab.WITNESS / case / "producer.jsonl.gz"):
        if row["stage"] == "intent":
            expected[row["id"]] = row["sha256"]
    pod = sorted(
        n
        for n, info in control.identities().items()
        if "-brokers-" in n and info["ready"]
    )[-1]
    output = root / "binary-record-captures" / uuid.uuid4().hex
    output.mkdir(parents=True)
    manifest = {"original_status": "failed", "topic": plan["topic"], "partitions": {}}
    for partition in range(plan["partitions"]):
        end = state["end_offsets"][str(partition)]
        args = lab.KUBE + [
            "exec",
            pod,
            "-c",
            "kafka",
            "--",
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server",
            control.BOOTSTRAP,
            "--topic",
            plan["topic"],
            "--partition",
            str(partition),
            "--offset",
            "earliest",
            "--max-messages",
            str(end),
            "--timeout-ms",
            "10000",
            "--command-property",
            "enable.auto.commit=false",
            "--formatter-property",
            "print.offset=true",
            "--formatter-property",
            "print.key=true",
            "--formatter-property",
            "print.value=true",
            "--formatter-property",
            "line.separator=" + SEPARATOR.decode(),
        ]
        raw = output / (str(partition) + ".raw")
        error = output / (str(partition) + ".stderr")
        with raw.open("xb") as stream, error.open("xb") as errors:
            process = subprocess.run(
                args,
                cwd=lab.ROOT,
                env=lab.ENV,
                stdout=stream,
                stderr=errors,
                timeout=120,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if process.returncode:
            raise RuntimeError(
                "native capture failed; raw output retained: " + str(output)
            )
        content = raw.read_bytes()
        frames = content.split(SEPARATOR)
        if frames[-1] != b"":
            raise ValueError("incomplete native record framing")
        digest = hashlib.sha256()
        count = 0
        for frame in frames[:-1]:
            header, key, payload = frame.split(b"\t", 2)
            if header != ("Offset:" + str(count)).encode():
                raise ValueError("unexpected native offset, gap or contaminated stdout")
            identity = key.decode("ascii")
            seq = int(identity.removeprefix(case + "-order-"))
            payload_hash = hashlib.sha256(payload).hexdigest()
            if (
                identity != case + "-order-" + str(seq)
                or expected.get(identity) != payload_hash
            ):
                raise ValueError("native body differs from durable intent")
            digest.update(count.to_bytes(8, "big", signed=True))
            digest.update(seq.to_bytes(8, "big"))
            digest.update(payload_hash.encode())
            count += 1
        if count != end or digest.hexdigest() != state["partition_sha256"][partition]:
            raise ValueError("independent Java/Rust physical record proofs disagree")
        packed = raw.with_suffix(".raw.gz")
        with packed.open("xb") as destination:
            with gzip.GzipFile(
                fileobj=destination, mode="wb", compresslevel=1
            ) as encoder:
                encoder.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        if gzip.decompress(packed.read_bytes()) != content:
            raise ValueError("compressed raw evidence differs")
        raw.unlink()
        manifest["partitions"][str(partition)] = {
            "records": count,
            "physical_sha256": digest.hexdigest(),
            "raw_bytes": len(content),
            "raw_sha256": hashlib.sha256(content).hexdigest(),
            "compressed_bytes": packed.stat().st_size,
        }
        print(
            "Preserved partition", partition, count, "full native records", flush=True
        )
    lab.save(output / "manifest.json", manifest)
    print("Original test remains FAILED. Complete binary capture:", output, flush=True)


if __name__ == "__main__":
    with (lab.STATE / "lifecycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
