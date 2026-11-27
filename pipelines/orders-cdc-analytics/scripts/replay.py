"""Explicit log-based recovery after a terminal job failure; never reset source/sinks."""

import json
import subprocess
import uuid

import lab


def main():
    path = lab.directory("replay")
    value = lab.fixture()
    previous = lab.api("/jobs/" + value["job"])
    lab.save(path / "previous-job.json", previous)
    if previous["state"] != "FAILED":
        raise RuntimeError("Replay requires the previous job to be terminal FAILED")
    if any(job["state"] == "RUNNING" for job in lab.api("/jobs/overview")["jobs"]):
        raise RuntimeError("Another streaming job is still running")
    # Rebuilding from earliest is safe only while every source partition is retained.
    for topic, partitions in (("cdc.public.orders", 4), ("cdc.public.customers", 1)):
        offsets = lab.command(
            "exec",
            "-T",
            "-e",
            "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m",
            "broker1",
            "/opt/kafka/bin/kafka-get-offsets.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--topic",
            topic,
            "--time",
            "earliest",
        )
        (path / (topic + "-earliest.txt")).write_text(offsets)
        lines = [
            line.split(":")
            for line in offsets.splitlines()
            if line.startswith(topic + ":")
        ]
        if (
            len(lines) != partitions
            or {int(line[1]) for line in lines} != set(range(partitions))
            or any(int(line[2]) != 0 for line in lines)
        ):
            raise RuntimeError(
                "Complete source history is not retained; do not replay a partial log"
            )
    case = uuid.uuid4().hex
    sql = (lab.ROOT / "sql/pipeline.sql").read_text()
    sql = sql.replace("orders_checkpointed", "replay_" + case + "_checkpointed")
    sql = sql.replace("orders_asynchronous", "replay_" + case + "_asynchronous")
    (path / "submitted.sql").write_text(sql)
    result = subprocess.run(
        [
            *lab.COMPOSE,
            "run",
            "--rm",
            "-T",
            "sql",
            "-Drest.address=jobmanager",
            "-Djobmanager.rpc.address=jobmanager",
            "-f",
            "/dev/stdin",
        ],
        cwd=lab.ROOT,
        input=sql,
        text=True,
        capture_output=True,
        timeout=240,
    )
    (path / "submission.log").write_text(result.stdout + result.stderr)
    if result.returncode or "[ERROR]" in result.stdout + result.stderr:
        raise RuntimeError("Replay submission failed; see preserved native output")
    jobs = lab.wait_for(
        lambda: [
            job
            for job in lab.api("/jobs/overview")["jobs"]
            if job["state"] == "RUNNING"
        ]
    )
    if len(jobs) != 1:
        raise RuntimeError("Expected exactly one replacement job")
    value.setdefault("failed_jobs", []).append(value["job"])
    value["job"] = jobs[0]["jid"]
    lab.save(lab.ROOT / "results/fixture.json", value)
    lab.save(
        path / "recovery.json",
        {
            "method": "complete retained Kafka log replay into existing sink tables",
            "source_reseeded": False,
            "sink_cleared": False,
            "fixture": value,
        },
    )
    lab.workload("verify", path)
    lab.native(path)
    lab.save(path / "status.json", {"passed": True})
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
