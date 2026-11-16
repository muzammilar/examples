"""Independent S3 requests and durable raw observations for the recovery lab.

This extends the existing Boto3 checksum oracle without changing other examples.
No mutation retries: each unique PUT has one fsynced attempt and one outcome.
"""

import json
import os
import sys
import time

import workload as w
from botocore.exceptions import BotoCoreError, ClientError


def append(stream, value):
    stream.write(json.dumps(value) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def writer(store):
    end = time.monotonic() + w.number("DURATION", "600", 60, 1800)
    run_id = w.load("case")["id"]
    with (w.ROOT / "churn.jsonl").open("x") as journal:
        count = 0
        while time.monotonic() < end and not (w.ROOT / "stop-churn").exists():
            key = f"churn/{run_id}/{count:08d}"
            data = w.body(key, 32768, count + 1)
            value = w.record(key, data, count + 1)
            append(journal, {"stage": "attempt", "time": time.time(), "object": value})
            started = time.monotonic()
            try:
                store.put(key, data, count + 1)
                outcome = {"stage": "ack"}
            except (BotoCoreError, ClientError, OSError) as error:
                outcome = {"stage": "ambiguous", "error": str(error)[:500]}
            append(
                journal,
                outcome
                | {
                    "key": key,
                    "time": time.time(),
                    "seconds": time.monotonic() - started,
                },
            )
            count += 1
            time.sleep(0.05)
    w.save(
        "writer",
        {"attempts": count, "stopped_by_controller": (w.ROOT / "stop-churn").exists()},
    )


def reader(store):
    expected, required = store.expected()
    pool = [expected[key] for key in sorted(required)]
    if not pool:
        raise RuntimeError("independent reader has no witnessed objects")
    end = time.monotonic() + w.number("DURATION", "600", 60, 1800)
    journal_path = w.ROOT / "churn.jsonl"
    # Tail complete, fsynced writer records. Keep partial lines until completed.
    offset, pending, attempts, recent = 0, b"", {}, []
    with (w.ROOT / "reader.jsonl").open("x") as journal:
        count = 0
        while time.monotonic() < end and not (w.ROOT / "stop-reader").exists():
            if journal_path.exists():
                with journal_path.open("rb") as source:
                    source.seek(offset)
                    data = source.read()
                    offset = source.tell()
                lines = (pending + data).split(b"\n")
                pending = lines.pop()
                for line in lines:
                    event = json.loads(line)
                    if event["stage"] == "attempt":
                        attempts[event["object"]["key"]] = event["object"]
                    elif event["stage"] in ["ack", "ambiguous"]:
                        value = attempts.pop(event["key"])
                        if event["stage"] == "ack":
                            recent.append(value)
                recent = recent[-1000:]
            value = (
                recent[(count // 2) % len(recent)]
                if count % 2 and recent
                else pool[(count // 2) % len(pool)]
            )
            started = time.monotonic()
            try:
                store.get(value)
                outcome = {"stage": "get"}
            except (BotoCoreError, ClientError, OSError) as error:
                outcome = {"stage": "get_error", "error": str(error)[:500]}
            except RuntimeError as error:
                # Wrong bytes/metadata violate the oracle, even if later repaired.
                append(
                    journal,
                    {
                        "stage": "wrong",
                        "key": value["key"],
                        "time": time.time(),
                        "error": str(error),
                    },
                )
                raise
            append(
                journal,
                outcome
                | {
                    "key": value["key"],
                    "time": time.time(),
                    "seconds": time.monotonic() - started,
                    "source": "current_ack" if value in recent else "historical_ack",
                },
            )
            count += 1
            time.sleep(0.05)
    w.save(
        "reader",
        {"requests": count, "stopped_by_controller": (w.ROOT / "stop-reader").exists()},
    )


if __name__ == "__main__":
    {"writer": writer, "reader": reader}[sys.argv[1]](w.Store())
