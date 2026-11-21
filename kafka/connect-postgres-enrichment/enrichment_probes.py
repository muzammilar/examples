"""Independent PostgreSQL writer/reader and fsynced external operation evidence."""

import gzip
import json
import os
import subprocess
import threading
import time
import uuid

import enrichment_lab as lab


class Journal:
    def __init__(self, path):
        self.fd = path.open("xb")
        self.stream = gzip.GzipFile(fileobj=self.fd, mode="wb", compresslevel=1)

    def append(self, event):
        self.stream.write((json.dumps(event) + "\n").encode())
        self.stream.flush()
        self.fd.flush()
        os.fsync(self.fd.fileno())

    def close(self):
        self.stream.close()
        self.fd.flush()
        os.fsync(self.fd.fileno())
        self.fd.close()


def events(path):
    with gzip.open(path, "rt") as stream:
        for line in stream:
            yield json.loads(line)


class Probes:
    def __init__(self, root):
        self.root = root
        self.stop_event = threading.Event()
        self.error = None
        self.acknowledged = 0
        self.last_version = 0
        self.reads = 0
        self.threads = [
            threading.Thread(target=self.run, args=(kind,), name="postgres-" + kind)
            for kind in ("writer", "reader")
        ]

    def start(self):
        for thread in self.threads:
            thread.start()

    def check(self):
        if self.error:
            raise RuntimeError("SQL probe failed") from self.error

    def run(self, kind):
        journal = Journal(self.root / ("postgres-" + kind + ".jsonl.gz"))
        last_read = 0
        try:
            while not self.stop_event.is_set():
                identity = uuid.uuid4().hex
                started = time.time()
                journal.append({"stage": "intent", "id": identity, "time": started})
                try:
                    if kind == "writer":
                        version = int(
                            lab.sql(
                                "WITH changed AS (UPDATE customers SET cdc_probe=cdc_probe+1 WHERE id='customer-0' RETURNING cdc_probe) INSERT INTO probe_operations(id,version) SELECT '"
                                + identity
                                + "',cdc_probe FROM changed RETURNING version"
                            ).splitlines()[0]
                        )
                        result = {"stage": "ack", "version": version}
                    else:
                        version = int(
                            lab.sql(
                                "SELECT cdc_probe FROM customers WHERE id='customer-0'"
                            )
                        )
                        if version < last_read:
                            raise ValueError("PostgreSQL read version moved backward")
                        last_read = version
                        result = {"stage": "read", "version": version}
                except (
                    RuntimeError,
                    TimeoutError,
                    subprocess.TimeoutExpired,
                    OSError,
                ) as error:
                    result = {
                        "stage": "ambiguous" if kind == "writer" else "read_error",
                        "error": str(error),
                    }
                result.update(
                    {
                        "time": time.time(),
                        "seconds": time.time() - started,
                        "id": identity,
                    }
                )
                journal.append(result)
                if result["stage"] == "ack":
                    self.last_version = version
                    self.acknowledged += 1
                elif result["stage"] == "read":
                    self.reads += 1
                if self.stop_event.wait(max(0, 1 - (time.time() - started))):
                    break
        except Exception as error:
            self.error = error
        finally:
            journal.close()

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=45)
        if any(t.is_alive() for t in self.threads):
            raise RuntimeError("SQL probe did not stop")
        self.check()


def reconcile(root, phases):
    from order_report import Histogram

    actual = json.loads(
        lab.sql(
            "SELECT coalesce(jsonb_object_agg(id,version),'{}'::jsonb) FROM probe_operations"
        )
    )
    intents = set()
    acknowledged = {}
    ambiguous = set()
    histograms = {}
    for case in sorted((lab.STATE / "cases").iterdir()):
        for kind in ("writer", "reader"):
            path = case / ("postgres-" + kind + ".jsonl.gz")
            if not path.exists():
                continue
            for event in events(path):
                stage = event["stage"]
                if kind == "writer":
                    if stage == "intent":
                        intents.add(event["id"])
                    elif stage == "ack":
                        acknowledged[event["id"]] = event["version"]
                    elif stage == "ambiguous":
                        ambiguous.add(event["id"])
                if case == root and stage != "intent":
                    phase = next(
                        (
                            p["phase"]
                            for p in reversed(phases)
                            if p["time"] <= event["time"]
                        ),
                        "setup",
                    )
                    histograms.setdefault(phase + ":" + stage, Histogram()).add(
                        event["seconds"], event["time"]
                    )
    if any(actual.get(key) != value for key, value in acknowledged.items()):
        raise RuntimeError("PostgreSQL lost/corrupted an acknowledged probe")
    if not set(actual) <= intents:
        raise RuntimeError("PostgreSQL has unwitnessed operations")
    return {
        "acknowledged": len(acknowledged),
        "loss": 0,
        "ambiguous_applied": len(ambiguous & actual.keys()),
        "ambiguous_absent": len(ambiguous - actual.keys()),
        "phases": {
            key: h.summary((h.last - h.first) if h.last != h.first else 0)
            for key, h in histograms.items()
        },
    }
