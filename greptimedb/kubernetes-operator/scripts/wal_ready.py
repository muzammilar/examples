"""Require initialized offset-zero no-ops before admitting this lab's first writes."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

project = "greptime-kind-support"
broker = project + "-wal0-1"
mode = sys.argv[1]
if mode not in ("prepare", "check"):
    raise ValueError(mode)
info = json.loads(subprocess.check_output(["docker", "inspect", broker], text=True))[0]
if info["Config"]["Labels"].get("com.docker.compose.project") != project:
    raise RuntimeError("refusing foreign WAL broker")


def rpk(*args):
    return subprocess.check_output(
        ["docker", "exec", broker, "rpk", *args], text=True, timeout=120
    )


topics = ["kind_telemetry_wal_" + str(index) for index in range(12)]
if mode == "prepare":
    existing = {
        topic["name"] for topic in json.loads(rpk("topic", "list", "--format=json"))
    }
    missing = [topic for topic in topics if topic not in existing]
    if missing:
        print(rpk("topic", "create", *missing, "--replicas", "3", "--partitions", "1"))
    print(rpk("cluster", "health", "--exit-when-healthy"))
else:
    initial = {
        topic: rpk(
            "topic",
            "consume",
            topic,
            "--offset",
            "0",
            "--num",
            "1",
            "--format",
            "%o key=%k value-bytes=%V\n",
        ).strip()
        for topic in topics
    }
    report = {"at": datetime.now(timezone.utc).isoformat(), "offset_zero": initial}
    report["safe"] = all(
        record == "0 key= value-bytes=0" for record in initial.values()
    )
    path = Path("results/wal-bootstrap-check.json")
    if path.exists():
        path.rename(
            path.with_stem(
                path.stem
                + "-before-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
            )
        )
    path.write_text(json.dumps(report, indent=2) + "\n")
    if not report["safe"]:
        raise RuntimeError(
            "unsafe WAL initialization; refusing ingestion; inspect wal-bootstrap-check.json and metasrv logs; do not reset populated data"
        )
    print("All 12 WAL topics reserve offset zero with a no-op")
