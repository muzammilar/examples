"""Native etcd hash at one fixed revision, not independently moving revisions."""

import json
import os
from pathlib import Path
import subprocess

endpoints = ",".join(f"http://etcd{i}:2379" for i in range(3))
base = [
    "docker",
    "compose",
    "exec",
    "-T",
    "etcd0",
    "/usr/local/bin/etcdctl",
    "--endpoints=" + endpoints,
]
status = json.loads(
    subprocess.check_output(base + ["endpoint", "status", "-w", "json"], text=True)
)
if len(status) != 3:
    raise RuntimeError("expected three metadata members")
revision = min(v["Status"]["header"]["revision"] for v in status)
hashes = json.loads(
    subprocess.check_output(
        base + ["endpoint", "hashkv", "--rev=" + str(revision), "-w", "json"], text=True
    )
)
if len(hashes) != 3 or len({v["HashKV"]["hash"] for v in hashes}) != 1:
    raise RuntimeError("etcd replicas differ at the same revision")
root = Path(os.environ.get("RESULTS_DIR", "results/verified"))
(root / "metadata-audit.json").write_text(
    json.dumps(
        {"revision": revision, "members": 3, "matching_hashes": True, "native": hashes},
        indent=2,
    )
    + "\n"
)
print("Three etcd members match at revision", revision, flush=True)
