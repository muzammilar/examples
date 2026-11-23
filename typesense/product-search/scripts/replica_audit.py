"""Direct local API readback of all metadata/vectors, with native Raft witnesses."""

import concurrent.futures
import json
import os
from pathlib import Path
import sys
import threading
import time

import httpx
import numpy as np

from version_history import Follower, digest, load_current
from workload import Fixture, META, save

ROOT = Path("/results")
MODE = os.environ.get("MODE", "cluster")
HEADERS = {"X-TYPESENSE-API-KEY": "local-search-key"}


class Lagging(RuntimeError):
    pass


def groups():
    if MODE not in {"single", "cluster", "sharded"}:
        raise ValueError("invalid MODE")
    return {
        shard: [
            f"http://ts{3 * shard + replica}:8108"
            for replica in range(1, 2 if MODE == "single" else 4)
        ]
        for shard in range(3 if MODE == "sharded" else 1)
    }


def snapshot(client):
    result = {}
    for shard, endpoints in groups().items():
        for endpoint in endpoints:
            response = client.get(endpoint + "/status")
            response.raise_for_status()
            status = response.json()
            health = client.get(endpoint + "/health")
            health.raise_for_status()
            if health.json() != {"ok": True}:
                raise Lagging("native read readiness incomplete: " + endpoint)
            if (
                status["state"] not in {"LEADER", "FOLLOWER"}
                or status["committed_index"] <= 0
            ):
                raise Lagging("native Raft peer not ready: " + endpoint)
            result[endpoint] = {"shard": shard, **status}
    for shard in groups():
        if (
            sum(v["shard"] == shard and v["state"] == "LEADER" for v in result.values())
            != 1
        ):
            raise Lagging("each independent group must have exactly one leader")
    return result


def converge(client, deadline):
    target = None
    while time.monotonic() < deadline:
        try:
            state = snapshot(client)
            if target is None:
                target = {
                    shard: max(
                        v["committed_index"]
                        for v in state.values()
                        if v["shard"] == shard
                    )
                    for shard in groups()
                }
            if all(
                v["committed_index"] >= target[v["shard"]] and v["queued_writes"] == 0
                for v in state.values()
            ):
                return state, target
        except (httpx.TransportError, httpx.HTTPStatusError, Lagging) as error:
            print("Waiting for native readiness:", error, flush=True)
        time.sleep(1)
    raise Lagging("native Raft/queue convergence deadline exceeded")


def json_lines(response):
    # Split LF bytes, not Unicode splitlines: product text can contain U+0085/U+2028.
    pending = b""
    for data in response.iter_bytes(chunk_size=64 * 1024):
        lines = (pending + data).split(b"\n")
        pending = lines.pop()
        if len(pending) > 4 * 1024 * 1024:
            raise RuntimeError("oversized exported row")
        for line in lines:
            if line:
                yield json.loads(line)
    if pending:
        yield json.loads(pending)


def verify(prefix):
    fixture = Fixture()
    floor = (
        json.loads((ROOT / "live-prefix/history.json").read_text())
        if prefix
        else load_current(ROOT).value()
    )
    target_dir = ROOT / "live-prefix" if prefix else ROOT
    follower = Follower(ROOT)
    current = follower.refresh()
    refresh_lock = threading.Lock()

    def allowed(key, version):
        nonlocal current
        if version == 0 or version in current.attempted.get(key, ()):
            return True
        with refresh_lock:
            current = follower.refresh()
            return version in current.attempted.get(key, ())

    def one(client, endpoint, shard):
        expected = {
            i
            for i, row in enumerate(fixture.products)
            if MODE != "sharded" or row["tenant"] % 3 == shard
        }
        seen = set()
        stale = 0
        with client.stream(
            "GET", endpoint + "/collections/products/documents/export"
        ) as response:
            response.raise_for_status()
            for actual in json_lines(response):
                if set(actual) != set(META) | {"embedding"}:
                    raise RuntimeError(
                        "exported fields differ from the complete schema: " + endpoint
                    )
                key, version = actual["id"], actual["version"]
                if (
                    type(key) is not str
                    or not key.isascii()
                    or not key.isdecimal()
                    or str(int(key)) != key
                ):
                    raise RuntimeError("invalid native product ID")
                number = int(key)
                if number not in expected or number in seen:
                    raise RuntimeError(
                        "duplicate, extra or misplaced product: " + endpoint + "/" + key
                    )
                if type(version) is not int or version < 0:
                    raise RuntimeError("invalid native version")
                if any(
                    type(actual[name]) is not int
                    for name in ["tenant", "stock", "price_cents"]
                ):
                    raise RuntimeError("invalid native metadata types")
                if {name: actual[name] for name in META} != fixture.row(
                    number, version
                ):
                    raise RuntimeError("metadata corruption: " + endpoint + "/" + key)
                if not allowed(key, version):
                    raise RuntimeError(
                        "native version has no external intent: " + endpoint + "/" + key
                    )
                vector = np.asarray(actual["embedding"], dtype=np.float32)
                if (
                    vector.shape != (384,)
                    or not np.isfinite(vector).all()
                    or not np.array_equal(vector, fixture.vectors[number])
                ):
                    raise RuntimeError("vector corruption: " + endpoint + "/" + key)
                if version < floor["ack_versions"].get(key, 0):
                    stale += 1
                seen.add(number)
        if seen != expected or stale:
            raise Lagging(f"{endpoint}: missing={len(expected - seen)}, stale={stale}")
        return {
            "shard": shard,
            "rows": len(seen),
            "vector_coordinates": len(seen) * 384,
            "missing_ids": 0,
            "stale_versions": 0,
            "wrong_payloads": 0,
            "misplaced_ids": 0,
        }

    started = time.monotonic()
    deadline = started + 900
    attempts = []
    with httpx.Client(
        headers=HEADERS,
        timeout=httpx.Timeout(120, connect=3),
        limits=httpx.Limits(max_connections=24),
    ) as client:
        before, watermark = converge(client, deadline)
        while True:
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    work = {
                        endpoint: pool.submit(one, client, endpoint, shard)
                        for shard, endpoints in groups().items()
                        for endpoint in endpoints
                    }
                    results = {
                        endpoint: future.result() for endpoint, future in work.items()
                    }
                break
            except (Lagging, httpx.TransportError, httpx.HTTPStatusError) as error:
                attempts.append({"time": time.time(), "lag_or_unavailable": str(error)})
                (target_dir / "convergence-attempts.json").write_text(
                    json.dumps(attempts, indent=2) + "\n"
                )
                if time.monotonic() >= deadline:
                    raise
                print("Waiting for direct-copy convergence:", error, flush=True)
                time.sleep(1)
        after = snapshot(client)
    result = {
        "mode": MODE,
        "fixture_id": json.loads((ROOT / "fixture.json").read_text())["id"],
        "acknowledgement_floor_sha256": digest(floor),
        "finished_at": time.time(),
        "copies": results,
        "rows_checked": sum(v["rows"] for v in results.values()),
        "vector_coordinates_checked": sum(
            v["vector_coordinates"] for v in results.values()
        ),
        "native_before": before,
        "native_after": after,
        "committed_watermarks": watermark,
        "fixture_sha256": fixture.manifest["sha256"],
        "seconds": time.monotonic() - started,
        "scope": "direct local API metadata and float32-coordinate readback; not an independent RocksDB decoder",
    }
    (target_dir / "replica-proof.json").write_text(json.dumps(result, indent=2) + "\n")
    follower.close()
    print(
        json.dumps(
            {
                key: result[key]
                for key in ["rows_checked", "vector_coordinates_checked", "seconds"]
            }
        ),
        flush=True,
    )


def drop():
    with httpx.Client(headers=HEADERS, timeout=120) as client:
        for endpoints in groups().values():
            for name in ["products", "curated"]:
                response = client.delete(endpoints[0] + "/collections/" + name)
                if response.status_code not in {200, 404}:
                    response.raise_for_status()
        deadline = time.monotonic() + 120
        while True:
            absent = True
            for endpoints in groups().values():
                for endpoint in endpoints:
                    for name in ["products", "curated"]:
                        response = client.get(endpoint + "/collections/" + name)
                        if response.status_code != 404:
                            response.raise_for_status()
                            absent = False
            if absent:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("collection removal did not converge")
            time.sleep(1)
    save(
        "cleanup",
        {
            "collections_absent_on_all_servers": True,
            "servers": sum(map(len, groups().values())),
        },
    )


if __name__ == "__main__":
    action = sys.argv[1]
    if action in {"verify", "prefix"}:
        verify(action == "prefix")
    elif action == "state":
        with httpx.Client(headers=HEADERS, timeout=5) as client:
            save("native-state", snapshot(client))
    elif action == "empty":
        with httpx.Client(headers=HEADERS, timeout=5) as client:
            for endpoints in groups().values():
                for endpoint in endpoints:
                    response = client.get(endpoint + "/collections")
                    response.raise_for_status()
                    if response.json() != []:
                        raise RuntimeError(
                            "unbound collections exist; refusing destructive loading"
                        )
    elif action == "drop":
        drop()
    else:
        raise ValueError(action)
