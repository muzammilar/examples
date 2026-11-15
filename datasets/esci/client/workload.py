"""Common immutable data, correctness, quality and timing contract for engine clients."""

import concurrent.futures
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import tempfile

import httpx
import numpy as np

META = [
    "id",
    "source_id",
    "locale",
    "name",
    "description",
    "brand",
    "color",
    "tenant",
    "stock",
    "price_cents",
    "version",
]
GAINS = {"E": 1.0, "S": 0.1, "C": 0.01, "I": 0.0}
RESULTS = Path("/results")


def parameter(name, default, low=1, high=1000000):
    value = int(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")
    return value


def save(name, value):
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / (name + ".json")
    with tempfile.NamedTemporaryFile(
        mode="w", dir=RESULTS, prefix="." + name + "-", delete=False
    ) as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        temporary = f.name
    os.replace(temporary, path)
    print(name, json.dumps(value), flush=True)


def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * p) - 1)]


def summary(samples):
    return {
        "samples": len(samples),
        "p50_ms": 1000 * percentile(samples, 0.5),
        "p95_ms": 1000 * percentile(samples, 0.95),
        "p99_ms": 1000 * percentile(samples, 0.99),
        "max_ms": 1000 * max(samples),
    }


def read_records(path):
    # JSONL separates records on LF; Unicode U+0085/U+2028 may occur inside JSON strings.
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


class Fixture:
    def __init__(self):
        root = Path("/corpus")
        self.manifest = json.loads((root / "manifest.json").read_text())
        for name in [
            "products.jsonl",
            "queries.jsonl",
            "vectors.f32",
            "query_vectors.f32",
            "exact.json",
        ]:
            with (root / name).open("rb") as f:
                if (
                    hashlib.file_digest(f, "sha256").hexdigest()
                    != self.manifest["sha256"][name]
                ):
                    raise RuntimeError("fixture checksum mismatch: " + name)
        self.products = read_records(root / "products.jsonl")
        self.queries = read_records(root / "queries.jsonl")
        n = len(self.products)
        d = self.manifest["parameters"]["dimension"]
        self.vectors = np.memmap(
            root / "vectors.f32", mode="r", dtype="<f4", shape=(n, d)
        )
        self.qvectors = np.memmap(
            root / "query_vectors.f32",
            mode="r",
            dtype="<f4",
            shape=(len(self.queries), d),
        )
        self.exact = json.loads((root / "exact.json").read_text())
        self.k = parameter("K", 10, 1, 10)

    def row(self, i, version=0):
        result = self.products[i].copy()
        if version:
            result.update(
                version=version,
                price_cents=self.products[i]["price_cents"] + version,
                stock=version % 100,
            )
        return result


class HTTP:
    def __init__(self, urls, headers=None):
        self.urls = urls
        self.rotation = itertools.cycle(urls)
        self.client = httpx.Client(
            timeout=httpx.Timeout(120, connect=3),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
            headers=headers,
        )

    def request(self, method, path, *, read=False, url=None, **kwargs):
        safe = read or method in ("GET", "HEAD")
        if url:
            choices = [url]
        elif safe:
            first = next(self.rotation)
            choices = [first] + [x for x in self.urls if x != first]
        else:
            choices = [self.urls[0]]
        errors = []
        for endpoint in choices:
            try:
                response = self.client.request(method, endpoint + path, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                errors.append(str(error))
                if not safe:
                    raise RuntimeError(
                        "unresolved request; no mutation replay: " + str(error)
                    ) from error
        raise RuntimeError("no healthy read endpoint: " + "; ".join(errors))

    def available(self):
        for url in self.urls:
            try:
                if self.client.get(url + "/health", timeout=3).is_success:
                    return url
            except httpx.TransportError:
                pass
        raise RuntimeError("no healthy write endpoint; no mutation sent")


def canonical(value):
    return {
        key: value.get(
            key, 0 if key in ["tenant", "stock", "price_cents", "version"] else ""
        )
        for key in META
    }


def oracle(adapter, fixture, minimum_versions=None, allow_attempted=None):
    versions = minimum_versions or {}
    attempted = allow_attempted or {}
    seen = set()
    bad = []
    for actual in adapter.rows():
        actual = canonical(actual)
        i = int(actual["id"])
        version = int(actual["version"])
        if not 0 <= i < len(fixture.products) or i in seen:
            raise RuntimeError("unexpected or duplicate ID")
        seen.add(i)
        minimum = versions.get(i, 0)
        if version < minimum or version > max(minimum, attempted.get(i, 0)):
            bad.append((i, "version", minimum, version))
        if actual != fixture.row(i, version):
            bad.append((i, "payload mismatch"))
    if len(seen) != len(fixture.products) or bad:
        raise RuntimeError(f"row oracle failed count={len(seen)} bad={bad[:5]}")
    return {
        "rows": len(seen),
        "missing_ids": 0,
        "duplicate_ids": 0,
        "invalid_rows": 0,
        "missing_acknowledged_versions": 0,
    }


def recall(fixture, index, ids, eligible=None):
    v = fixture.qvectors[index]
    if eligible is None:
        expected = fixture.exact[index]
        cutoff = expected["scores"][min(fixture.k, len(expected["scores"])) - 1]
    else:
        scores = fixture.vectors[eligible] @ v
        cutoff = float(
            np.partition(scores, -min(fixture.k, len(scores)))[
                -min(fixture.k, len(scores))
            ]
        )
    # Include boundary ties rather than penalizing equivalent equal-distance IDs.
    unique = set(map(int, ids))
    valid = [
        i
        for i in unique
        if 0 <= i < len(fixture.products) and (eligible is None or i in eligible)
    ]
    correct = sum(float(fixture.vectors[i] @ v) >= cutoff - 1e-5 for i in valid)
    denominator = fixture.k if eligible is None else min(fixture.k, len(eligible))
    return min(correct, denominator) / denominator


def ndcg(labels, ids, k):
    ideal = sorted((GAINS[x] for x in labels.values()), reverse=True)[:k]
    best = sum(value / math.log2(i + 2) for i, value in enumerate(ideal))
    if not best:
        return None
    actual = sum(
        GAINS.get(labels.get(str(pid), "I"), 0) / math.log2(i + 2)
        for i, pid in enumerate(ids[:k])
    )
    return actual / best


def correctness(adapter, fixture):
    checked = oracle(adapter, fixture)
    quality = []
    filtered = []
    for index, query in enumerate(
        fixture.queries[: parameter("CHECK_QUERIES", 50, 1, 1000)]
    ):
        result = adapter.search(
            query["text"], fixture.qvectors[index], mode="dense", k=fixture.k
        )
        ids = [str(x["id"]) for x in result]
        if len(ids) != fixture.k or len(set(ids)) != len(ids):
            raise RuntimeError("dense search returned short/duplicate results")
        quality.append(recall(fixture, index, ids))
        if index < 10:
            tenant = fixture.products[fixture.exact[index]["ids"][0]]["tenant"]
            eligible = np.array(
                [i for i, p in enumerate(fixture.products) if p["tenant"] == tenant],
                dtype=np.int64,
            )
            result = adapter.search(
                query["text"],
                fixture.qvectors[index],
                mode="dense",
                k=fixture.k,
                tenant=tenant,
            )
            ids = [str(x["id"]) for x in result]
            if len(set(ids)) != len(ids) or any(
                fixture.products[int(i)]["tenant"] != tenant for i in ids
            ):
                raise RuntimeError("filtered query leaked or duplicated an ID")
            if len(ids) != min(fixture.k, len(eligible)):
                raise RuntimeError(
                    "filtered search under-returned despite sufficient eligible products"
                )
            filtered.append(recall(fixture, index, ids, eligible))
    strength = adapter.strength_test() if hasattr(adapter, "strength_test") else {}
    replicas = (
        adapter.verify_replicas(fixture) if hasattr(adapter, "verify_replicas") else {}
    )
    save(
        "correctness",
        {
            "replicas": replicas,
            "oracle": checked,
            "dense_recall_at_k": statistics.mean(quality),
            "filtered_recall_at_k": statistics.mean(filtered),
            "checked_queries": len(quality),
            "strength": strength,
            "topology": adapter.stats(),
        },
    )


def benchmark(adapter, fixture):
    outputs = {}
    limit = min(parameter("BENCH_QUERIES", 1000, 10, 10000), len(fixture.queries))
    for mode in adapter.modes:
        for concurrency in [1, 8]:
            for i in range(min(16, limit)):
                adapter.search(
                    fixture.queries[i]["text"],
                    fixture.qvectors[i],
                    mode=mode,
                    k=fixture.k,
                )

            def one(i):
                start = time.perf_counter()
                hits = adapter.search(
                    fixture.queries[i]["text"],
                    fixture.qvectors[i],
                    mode=mode,
                    k=fixture.k,
                )
                elapsed = time.perf_counter() - start
                ids = [str(hit["id"]) for hit in hits]
                if len(set(ids)) != len(ids) or any(
                    not 0 <= int(pid) < len(fixture.products) for pid in ids
                ):
                    raise RuntimeError("bad benchmark result IDs")
                return elapsed, recall(fixture, i, ids) if mode == "dense" else None

            start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                results = list(pool.map(one, range(limit)))
            wall = time.perf_counter() - start
            samples = [x[0] for x in results]
            output = summary(samples) | {
                "seconds": wall,
                "qps": limit / wall,
                "latency_seconds": samples,
                "failures": 0,
            }
            if mode == "dense":
                output["recall_at_k"] = statistics.mean(x[1] for x in results)
            outputs[f"{mode}/clients-{concurrency}"] = output
    judged = {}
    for mode in adapter.modes:
        scores = []
        empty = 0
        for i, query in enumerate(
            fixture.queries[: min(limit, parameter("QUALITY_QUERIES", 100, 1, 1000))]
        ):
            hits = adapter.search(
                query["text"],
                fixture.qvectors[i],
                mode=mode,
                k=fixture.k,
                ids=list(query["judged"]),
            )
            ids = [str(x["id"]) for x in hits]
            if any(pid not in query["judged"] for pid in ids):
                raise RuntimeError("candidate filter leaked")
            value = ndcg(query["judged"], ids, fixture.k)
            if value is None:
                empty += 1
            else:
                scores.append(value)
        judged[mode] = {
            "candidate_ndcg_at_k": statistics.mean(scores) if scores else None,
            "scorable_queries": len(scores),
            "zero_idcg_queries": empty,
        }
    save(
        "benchmark",
        {
            "fixture_parameters": fixture.manifest["parameters"],
            "fixture_sha256": fixture.manifest["sha256"],
            "k": fixture.k,
            "results": outputs,
            "candidate_ranking": judged,
            "gains": GAINS,
            "topology": adapter.stats(),
            "scope": "warm request latency with precomputed vectors; 16 query warmups; no embedding cost; candidate labels are not exhaustive full-corpus truth",
        },
    )


def journal(label):
    path = RESULTS / (label + ".jsonl")
    f = path.open("w")

    def record(value):
        f.write(json.dumps(value) + "\n")
        f.flush()
        os.fsync(f.fileno())

    return f, record


def churn(adapter, fixture):
    duration = parameter("DURATION", 75, 10, 300)
    batch = parameter("UPDATE_BATCH", 10, 1, 100)
    start = time.monotonic()
    attempts = 0
    acked = 0
    errors = []
    latencies = []
    searches = 0
    search_errors = []
    f, record = journal("churn")
    with f:
        while time.monotonic() - start < duration:
            rows = [
                fixture.row(
                    (attempts * batch + j) % len(fixture.products), attempts + 1
                )
                for j in range(batch)
            ]
            record(
                {
                    "stage": "attempt",
                    "monotonic": time.monotonic(),
                    "rows": [{k: r[k] for k in ["id", "version"]} for r in rows],
                }
            )
            t = time.monotonic()
            try:
                adapter.update(rows)
                latencies.append(time.monotonic() - t)
                acked += len(rows)
                record(
                    {
                        "stage": "ack",
                        "monotonic": time.monotonic(),
                        "rows": [{k: r[k] for k in ["id", "version"]} for r in rows],
                    }
                )
            except Exception as error:
                errors.append(str(error)[:500])
                record({"stage": "ambiguous", "error": str(error)[:500]})
            try:
                index = attempts % len(fixture.queries)
                hits = adapter.search(
                    fixture.queries[index]["text"],
                    fixture.qvectors[index],
                    mode="dense",
                    k=fixture.k,
                )
                ids = [str(hit["id"]) for hit in hits]
                if len(ids) != fixture.k or len(set(ids)) != len(ids):
                    raise RuntimeError("short/duplicate live search")
                searches += 1
                record(
                    {"stage": "search", "monotonic": time.monotonic(), "hits": len(ids)}
                )
            except Exception as error:
                search_errors.append(str(error)[:500])
                record(
                    {
                        "stage": "search_error",
                        "monotonic": time.monotonic(),
                        "error": str(error)[:500],
                    }
                )
            attempts += 1
            time.sleep(0.1)
    save(
        "churn",
        {
            "searches_succeeded": searches,
            "search_errors": search_errors,
            "attempted_batches": attempts,
            "acknowledged_updates": acked,
            "ambiguous_requests": errors,
            "successful_batches": summary(latencies) if latencies else None,
        },
    )


def recovery_metrics(records, fault):
    fault_at = fault["fault_monotonic"]
    restart_at = fault["restart_monotonic"]
    stamps = [r["monotonic"] for r in records if "monotonic" in r]
    if not min(stamps) <= fault_at <= max(stamps) or restart_at < fault_at:
        raise RuntimeError(
            "fault marker and journal must use the same Linux monotonic clock"
        )
    current_attempt = None
    first_failed = None
    acks = []
    searches = []
    search_errors = []
    for record in records:
        stage = record["stage"]
        if stage == "attempt":
            current_attempt = record["monotonic"]
        elif stage == "ack":
            acks.append((current_attempt, record["monotonic"]))
        elif (
            stage == "ambiguous"
            and current_attempt >= fault_at
            and first_failed is None
        ):
            first_failed = current_attempt
        elif stage == "search":
            searches.append(record["monotonic"])
        elif stage == "search_error":
            search_errors.append(record["monotonic"])
    # An ACK in the small fault-request/SIGKILL window is not evidence of recovery.
    resumed = [
        ack
        for attempt, ack in acks
        if attempt > max(fault_at, first_failed or fault_at)
    ]
    ack_times = [ack for _, ack in acks]
    return {
        "acks_after_fault": sum(attempt > fault_at for attempt, _ in acks),
        "acks_while_node_absent": sum(
            ack < restart_at
            and attempt
            > (
                first_failed
                if first_failed is not None and first_failed < restart_at
                else fault_at
            )
            for attempt, ack in acks
        ),
        "searches_while_node_absent": sum(fault_at < t < restart_at for t in searches),
        "failed_searches_while_node_absent": sum(
            fault_at < t < restart_at for t in search_errors
        ),
        "maximum_ack_gap_seconds": max(
            (b - a for a, b in zip(ack_times, ack_times[1:])), default=0
        ),
        "first_ack_after_fault_seconds": min(resumed) - fault_at if resumed else None,
        "write_recovery_seconds": min(resumed) - first_failed
        if resumed and first_failed is not None
        else None,
        "write_recovery_scope": "first failed new attempt to first subsequent acknowledged new batch; null when no new attempt failed",
    }


def reconcile(adapter, fixture):
    versions = {}
    attempted = {}
    records = read_records(RESULTS / "churn.jsonl")
    for value in records:
        if value["stage"] not in ("attempt", "ack"):
            continue
        target = attempted if value["stage"] == "attempt" else versions
        for row in value["rows"]:
            target[int(row["id"])] = max(
                target.get(int(row["id"]), 0), int(row["version"])
            )
    if not versions:
        raise RuntimeError("fault workload produced no acknowledgements")
    checked = oracle(adapter, fixture, versions, attempted)
    replicas = (
        adapter.verify_replicas(fixture, versions, attempted)
        if hasattr(adapter, "verify_replicas")
        else {}
    )
    fault_path = RESULTS / "fault.json"
    fault = json.loads(fault_path.read_text()) if fault_path.exists() else {}
    save(
        "reconciliation",
        checked
        | {
            "replicas": replicas,
            "acknowledged_unique_ids": len(versions),
            **recovery_metrics(records, fault),
            "topology": adapter.stats(),
        },
    )


def main(Adapter):
    fixture = Fixture()
    adapter = Adapter(fixture)
    action = sys.argv[1]
    if action == "load":
        start = time.perf_counter()
        result = adapter.load()
        seconds = time.perf_counter() - start
        save(
            "load",
            {
                "rows": len(fixture.products),
                "seconds": seconds,
                "rows_per_second": len(fixture.products) / seconds,
                "details": result,
            },
        )
    elif action == "test":
        correctness(adapter, fixture)
    elif action == "benchmark":
        benchmark(adapter, fixture)
    elif action == "churn":
        churn(adapter, fixture)
    elif action == "reconcile":
        reconcile(adapter, fixture)
    elif action == "stats":
        save("stats", adapter.stats())
    elif action == "maintenance":
        save("maintenance", adapter.maintenance())
    else:
        raise ValueError("unknown action " + action)
