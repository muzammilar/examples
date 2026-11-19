"""Audit independent SDK views of one catalog, not independent durable replicas."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

import httpx
import numpy as np
from client import Adapter
from workload import Fixture, META, parameter, recall

ROOT = Path("/results")
NODES = ["writer"] + (
    [] if os.environ.get("MODE") == "single" else ["reader1", "reader2", "reader3"]
)
SOURCE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def request(node, action, body=None):
    with httpx.Client(timeout=120, trust_env=False) as client:
        response = client.post(f"http://{node}:8080/{action}", json=body or {})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("invalid native SDK reply")
        return result


def state():
    result = {}
    for node in NODES:
        try:
            result[node] = request(node, "stats")
        except Exception as error:
            result[node] = {"error": str(error)}
    return result


def check_state(states, fence=None):
    for node in NODES:
        s = states[node]
        if "error" in s:
            return False
        if (
            s["readonly"] != (node != "writer")
            or s["rows"] != int(os.environ.get("DOCUMENTS", "100000"))
            or s["catalog_version"] < 1
            or s["lancedb_version"] != "0.41.0"
        ):
            raise RuntimeError("wrong catalog membership/version/role: " + repr(s))
        if {tuple(i["columns"]) for i in s["indexes"]} != {
            ("embedding",),
            ("tenant",),
            ("text",),
        }:
            raise RuntimeError("partial or wrong index configuration")
        if fence is not None and s["catalog_version"] < fence:
            return False
    if len({s["fixture_sha256"] for s in states.values()}) != 1:
        raise RuntimeError("views use different fixtures")
    return True


def wait_healthy():
    deadline = time.monotonic() + 180
    fence = None
    captured = None
    while time.monotonic() < deadline:
        states = state()
        if fence is None and check_state(states):
            fence = states["writer"]["catalog_version"]
            captured = time.time()
        if fence is not None and check_state(states, fence):
            return {
                "views": states,
                "catalog_barrier": {
                    "version": fence,
                    "captured": captured,
                    "confirmed": time.time(),
                },
                "verifier_source_sha256": SOURCE,
            }
        time.sleep(1)
    raise RuntimeError(
        "SDK views did not reach captured catalog version: " + repr(states)
    )


def histories(active_case=None):
    intents, acknowledgements = {}, {}
    counts = {"attempted_rows": 0, "acknowledged_rows": 0, "ambiguous_rows": 0}
    files = sorted(ROOT.glob("cases/*/writer.jsonl"))
    for file in files:
        pending = {}
        with file.open() as stream:
            for line in stream:
                if not line.endswith("\n") and file.parent.name == active_case:
                    break
                event = json.loads(line)
                stage, rows, identity = (
                    event["stage"],
                    event["rows"],
                    event["request_id"],
                )
                if not isinstance(rows, list) or not rows:
                    raise RuntimeError("empty witnessed mutation")
                if stage == "attempt":
                    if identity in pending:
                        raise RuntimeError("duplicate intent")
                    pending[identity] = rows
                    counts["attempted_rows"] += len(rows)
                    for row in rows:
                        key = row["id"], row["version"]
                        if key in intents:
                            raise RuntimeError("reused operation identity")
                        intents[key] = row
                elif stage in ("ack", "ambiguous"):
                    if pending.pop(identity, None) != rows:
                        raise RuntimeError("outcome without identical intent")
                    counts[
                        "acknowledged_rows" if stage == "ack" else "ambiguous_rows"
                    ] += len(rows)
                    if stage == "ack":
                        for row in rows:
                            acknowledgements[row["id"]] = max(
                                acknowledgements.get(row["id"], 0), row["version"]
                            )
                else:
                    raise RuntimeError("unknown outcome")
        counts["ambiguous_rows"] += sum(map(len, pending.values()))
    return intents, acknowledgements, counts, files


def check_row(fixture, row, intents, acks):
    if set(row) != set(META) | {"vector_sha256"}:
        raise RuntimeError("missing/unexpected row fields")
    pid, version = row["id"], row["version"]
    if (
        not isinstance(pid, str)
        or not pid.isascii()
        or not pid.isdecimal()
        or str(int(pid)) != pid
        or not 0 <= int(pid) < len(fixture.products)
    ):
        raise RuntimeError("invalid ID")
    if type(version) is not int or version < acks.get(pid, 0):
        raise RuntimeError("acknowledged version lost")
    expected = fixture.row(int(pid), version)
    if any(type(row[k]) is not type(expected[k]) for k in META):
        raise RuntimeError('wrong metadata field type')
    if {k: row[k] for k in META} != expected:
        raise RuntimeError("wrong complete metadata body")
    mutable = {
        k: expected[k] for k in ["id", "tenant", "stock", "price_cents", "version"]
    }
    if version > 0 and intents.get((pid, version)) != mutable:
        raise RuntimeError("unwitnessed update")
    vector = np.asarray(fixture.vectors[int(pid)], dtype="<f4")
    if (
        vector.shape != (384,)
        or hashlib.sha256(vector.tobytes()).hexdigest() != row["vector_sha256"]
    ):
        raise RuntimeError("stored vector hash differs")
    return int(pid)


def verify(fixture):
    deadline = time.monotonic() + 120
    while request("writer", "stats")["pending_mutations"]:
        if time.monotonic() >= deadline:
            raise RuntimeError("timed-out SDK mutations did not drain")
        time.sleep(0.2)
    healthy = wait_healthy()
    version = healthy["views"]["writer"]["catalog_version"]
    intents, acks, counts, files = histories()
    proofs = {}
    for node in NODES:
        rows = {}
        with httpx.Client(timeout=120, trust_env=False) as client:
            with client.stream(
                "GET", f"http://{node}:8080/rows?vectors=hash"
            ) as response:
                response.raise_for_status()
                if int(response.headers["X-Catalog-Version"]) != version:
                    raise RuntimeError("catalog changed during quiescent proof")
                for line in response.iter_lines():
                    row = json.loads(line)
                    pid = check_row(fixture, row, intents, acks)
                    if pid in rows:
                        raise RuntimeError("duplicate product")
                    rows[pid] = row
        if len(rows) != len(fixture.products):
            raise RuntimeError("incomplete catalog")
        raw = ROOT / f"view-{time.time_ns()}-{node}.jsonl.gz"
        logical = hashlib.sha256()
        with gzip.open(raw, "wb") as stream:
            for pid in range(len(fixture.products)):
                body = (
                    json.dumps(rows[pid], sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
                logical.update(body)
                stream.write(body)
        with raw.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        proofs[node] = {
            "rows": len(rows),
            "vector_coordinates": len(rows) * 384,
            "logical_sha256": logical.hexdigest(),
            "raw": raw.name,
            "raw_sha256": digest,
            "catalog_version": version,
        }
    if len({p["logical_sha256"] for p in proofs.values()}) != 1:
        raise RuntimeError("independent views disagree")
    after = state()
    if not check_state(after, version) or any(
        s["catalog_version"] != version for s in after.values()
    ):
        raise RuntimeError("catalog changed during final proof")
    return counts | {
        "scope": "views-of-one-shared-catalog",
        "verifier_source_sha256": SOURCE,
        "histories": len(files),
        "distinct_acknowledged_ids": len(acks),
        "acknowledged_loss": 0,
        "corruption": 0,
        "views": proofs,
        "native_before": healthy,
        "native_after": after,
    }


def search_checks(fixture, adapter):
    dense, filtered = [], []
    for index, query in enumerate(
        fixture.queries[: parameter("CHECK_QUERIES", 50, 1, 1000)]
    ):
        hits = adapter.search(query["text"], fixture.qvectors[index], "dense", 10)
        ids = [h["id"] for h in hits]
        if len(ids) != 10 or len(set(ids)) != 10:
            raise RuntimeError("short/duplicate search")
        dense.append(recall(fixture, index, ids))
        if index < 10:
            tenant = fixture.products[fixture.exact[index]["ids"][0]]["tenant"]
            eligible = np.array(
                [i for i, p in enumerate(fixture.products) if p["tenant"] == tenant],
                dtype=np.int64,
            )
            hits = adapter.search(
                query["text"], fixture.qvectors[index], "dense", 10, tenant=tenant
            )
            ids = [h["id"] for h in hits]
            if (
                len(ids) != 10
                or len(set(ids)) != 10
                or any(h["tenant"] != tenant for h in hits)
            ):
                raise RuntimeError("tenant leak or short filtered search")
            filtered.append(recall(fixture, index, ids, eligible))
    return {
        "dense_queries": len(dense),
        "dense_recall_at_10": statistics.mean(dense),
        "filtered_queries": len(filtered),
        "filtered_recall_at_10": statistics.mean(filtered),
        "versioned_catalog": adapter.strength_test(),
    }


def save(name, value):
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / (name + ".json")).open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main():
    action = sys.argv[1]
    if action == "state":
        result = state()
    elif action == "healthy":
        result = wait_healthy()
    else:
        fixture = Fixture()
        adapter = Adapter(fixture)
        if action == "up":
            # Missing table must not silently erase the meaning of preserved acknowledgements.
            states = state()
            if "error" in states["writer"] and list(ROOT.glob("cases/*/writer.jsonl")):
                raise RuntimeError(
                    "refusing initialization over prior histories; diagnose retained state"
                )
            save("load-" + str(time.time_ns()), request("writer", "load"))
            result = verify(fixture)
        elif action == "verify":
            result = verify(fixture)
        elif action == "search-check":
            result = search_checks(fixture, adapter)
        elif action == "maintenance":
            result = request("writer", "maintenance")
        elif action == "drop":
            result = request("writer", "drop")
        else:
            raise ValueError(action)
    save("command-" + action, result)


if __name__ == "__main__":
    main()
