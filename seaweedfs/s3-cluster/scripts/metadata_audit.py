"""Native etcd hashes at one revision that is current on all three members."""

import json
import os
import subprocess
from pathlib import Path


def validate(hashes, revision):
    if len(hashes) != 3 or len({v["HashKV"]["hash"] for v in hashes}) != 1:
        raise RuntimeError("etcd replicas differ at the same revision")
    values = [v["HashKV"] for v in hashes]
    if (
        any(
            v["header"]["revision"] != revision or v["hash_revision"] != revision
            for v in values
        )
        or len({v["header"]["member_id"] for v in values}) != 3
        or len({v["header"]["cluster_id"] for v in values}) != 1
        or len({v["header"]["raft_term"] for v in values}) != 1
    ):
        raise RuntimeError(
            "matching historical hashes do not prove current metadata convergence"
        )


def main():
    endpoints = ",".join(f"http://etcd{i}:2379" for i in range(3))
    base = [
        "docker",
        "compose",
        "--project-name",
        os.environ.get("COMPOSE_PROJECT_NAME", "seaweedfs-s3-cluster"),
        "exec",
        "-T",
        "etcd0",
        "/usr/local/bin/etcdctl",
        "--endpoints=" + endpoints,
    ]
    status = json.loads(
        subprocess.check_output(
            base + ["endpoint", "status", "-w", "json"], text=True, timeout=30
        )
    )
    if len(status) != 3:
        raise RuntimeError("expected three metadata members")
    revision = min(v["Status"]["header"]["revision"] for v in status)
    hashes = json.loads(
        subprocess.check_output(
            base + ["endpoint", "hashkv", "--rev=" + str(revision), "-w", "json"],
            text=True,
            timeout=30,
        )
    )
    validate(hashes, revision)
    root = Path(os.environ.get("RESULTS_DIR", "results/recovery"))
    (root / "metadata-audit.json").write_text(
        json.dumps(
            {
                "revision": revision,
                "members": 3,
                "matching_hashes": True,
                "current_revision_on_all_members": True,
                "status": status,
                "native": hashes,
            },
            indent=2,
        )
        + "\n"
    )
    print("Three etcd members match at current revision", revision, flush=True)


if __name__ == "__main__":
    main()
