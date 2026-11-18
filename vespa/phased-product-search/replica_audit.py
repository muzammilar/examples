"""Strict full-body/vector proof from native, physical-node-directed Vespa gets."""

import json
import hashlib
import os
from pathlib import Path
import sys
import time

import httpx
import numpy as np

from native import canonical_metadata, iter_copy
from version_history import Follower, digest
from workload import Fixture

ROOT = Path("/results")
MODE = os.environ.get("MODE", "cluster")
COUNT = 1 if MODE == "single" else 9 if MODE == "sharded" else 3


def state(client):
    controllers = 1 if MODE == "single" else 3
    errors = []
    for node in range(controllers):
        url = f"http://node{node}:19050/cluster/v2/catalog?recursive=true"
        try:
            response = client.get(url)
            response.raise_for_status()
            value = response.json()
            if "service" not in value:
                raise RuntimeError("native cluster state lacks services")
            return {"endpoint": str(response.url), "requested": url, "state": value}
        except (httpx.HTTPError, ValueError, RuntimeError) as error:
            errors.append(str(error))
    raise RuntimeError("native controller unavailable: " + str(errors))


def snapshot():
    with httpx.Client(timeout=10, follow_redirects=True) as client:
        native = state(client)
        metrics = {}
        for node in range(COUNT):
            host = f"content{node}" if MODE == "sharded" else f"node{node}"
            response = client.get(
                f"http://{host}:19092/metrics/v2/values?consumer=vespa"
            )
            response.raise_for_status()
            metrics[host] = response.json()
        return {"native": native, "metrics": metrics, "time": time.time()}


def check(prefix):
    fixture = Fixture()
    floor = json.loads((ROOT / "floor.json").read_text())
    follower = Follower(ROOT)
    current = follower.refresh()
    n = len(fixture.products)
    groups = 3 if MODE == "sharded" else COUNT
    seen = [np.zeros(n, dtype=np.bool_) for _ in range(groups)]
    counts = {}
    final_versions = np.full(n, -1, dtype=np.int64)
    optional_versions = 0
    stale = 0
    started = time.monotonic()
    try:
        for node in range(COUNT):
            group = node // 3 if MODE == "sharded" else node
            count = 0
            for index, document in enumerate(
                iter_copy(ROOT / f"{prefix}-{node}.json.gz", n)
            ):
                if document is None:
                    continue
                if seen[group][index]:
                    raise RuntimeError(
                        f"duplicate native copy within group {group}: {index}"
                    )
                fields = document["fields"]
                version = fields["version"]
                if type(version) is not int or version < 0:
                    raise RuntimeError("invalid native version")
                key = str(index)
                if version and version not in current.attempted.get(key, ()):
                    current = follower.refresh()
                    if version not in current.attempted.get(key, ()):
                        raise RuntimeError("native version lacks external intent")
                actual = canonical_metadata(fields, fixture.row(index, version))
                if actual != fixture.row(index, version):
                    raise RuntimeError(
                        f"wrong native metadata on node {node}, ID {index}"
                    )
                if any(
                    type(fields[name]) is not int
                    for name in ["pid", "tenant", "stock", "price_cents"]
                ):
                    raise RuntimeError("invalid native integer types")
                tensor = fields["embedding"]
                if (
                    not isinstance(tensor, dict)
                    or tensor.get("type") != "tensor<float>(x[384])"
                ):
                    raise RuntimeError("wrong native tensor type/dimension")
                values = tensor.get("values")
                vector = np.asarray(values, dtype=np.float32)
                if (
                    vector.shape != (384,)
                    or not np.isfinite(vector).all()
                    or not np.array_equal(vector, fixture.vectors[index])
                ):
                    raise RuntimeError(
                        f"wrong native vector on node {node}, ID {index}"
                    )
                stale += version < floor["ack_versions"].get(key, 0)
                optional_versions += version > floor["ack_versions"].get(key, 0)
                if prefix == "final":
                    if final_versions[index] < 0:
                        final_versions[index] = version
                    elif final_versions[index] != version:
                        raise RuntimeError(
                            "native copies disagree after writer stopped: " + key
                        )
                seen[group][index] = True
                count += 1
            counts[str(node)] = count
            if MODE == "sharded" and not 0 < count < n:
                raise RuntimeError("native content host lacks proper shard subset")
        missing = sum(n - int(group.sum()) for group in seen)
        if missing or stale:
            raise RuntimeError(
                f"native copy convergence incomplete: missing={missing}, stale={stale}"
            )
        value = {
            "rows_per_node": counts,
            "rows_checked": sum(counts.values()),
            "vector_coordinates_checked": sum(counts.values()) * 384,
            "directed_gets": n * COUNT,
            "groups": groups,
            "missing": 0,
            "stale": 0,
            "wrong_payloads": 0,
            "versions_above_ack_floor": optional_versions,
            "all_native_versions_agree": True if prefix == "final" else None,
            "floor_sha256": digest(floor),
            "fixture_sha256": fixture.manifest["sha256"],
            "seconds": time.monotonic() - started,
            "verifier_sha256": {
                name: hashlib.sha256((Path("/app") / name).read_bytes()).hexdigest()
                for name in ["replica_audit.py", "native.py", "version_history.py"]
            },
            "scope": "native physical-node-directed get; full metadata and every float32 coordinate; not independent disk decoding",
        }
        (ROOT / (prefix + "-proof.json")).write_text(json.dumps(value, indent=2) + "\n")
        print(json.dumps(value), flush=True)
    finally:
        follower.close()


if __name__ == "__main__":
    if sys.argv[1] == "cluster":
        with httpx.Client(timeout=5, follow_redirects=True) as client:
            value = state(client)
        (ROOT / "cluster-state.json").write_text(json.dumps(value, indent=2) + "\n")
        print(json.dumps(value), flush=True)
    elif sys.argv[1] == "state":
        value = snapshot()
        (ROOT / "native-state.json").write_text(json.dumps(value, indent=2) + "\n")
        print(json.dumps(value["native"]), flush=True)
    else:
        check(sys.argv[1])
