"""Application-managed tenant routing across three independent Typesense clusters."""

import concurrent.futures
import heapq
import math
import statistics
import sys
import time

import numpy as np

from client import Adapter
from workload import Fixture, benchmark, main, oracle, parameter, recall, save, summary


def shard_for(tenant):
    if type(tenant) is not int or tenant < 0:
        raise ValueError("tenant must be a nonnegative integer")
    return tenant % 3


class RoutedAdapter:
    # Only distance scores have a defined cross-cluster merge in this example.
    modes = ["dense"]

    def __init__(self, fixture):
        self.fixture = fixture
        self.shards = [
            Adapter(
                fixture,
                [f"http://ts{shard * 3 + replica}:8108" for replica in range(1, 4)],
            )
            for shard in range(3)
        ]
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=24)

    def load(self):
        groups = [[], [], []]
        for row in self.fixture.products:
            groups[shard_for(row["tenant"])].append(row)
        # Independent Raft groups can ingest concurrently.
        tasks = [
            self.pool.submit(adapter.load, rows)
            for adapter, rows in zip(self.shards, groups)
        ]
        for task in tasks:
            task.result()
        return {
            "routing": "application-managed tenant % 3; no native Typesense sharding",
            "shards": 3,
            "replicas_per_shard": 3,
            "rows_per_shard": [len(rows) for rows in groups],
        }

    def update(self, rows):
        groups = [[], [], []]
        for row in rows:
            # Tenant changes require an explicit migration, never a second copy on another shard.
            if row["tenant"] != self.fixture.products[int(row["id"])]["tenant"]:
                raise ValueError("tenant mutation requires migration")
            groups[shard_for(row["tenant"])].append(row)
        # A cross-shard batch is NOT atomic: if any call fails, the common journal
        # marks the whole batch ambiguous, allowing attempted versions on all shards.
        for adapter, group in zip(self.shards, groups):
            if group:
                adapter.update(group)

    def search(self, text, vector, mode, k=10, ids=None, tenant=None):
        if tenant is not None:
            return self.shards[shard_for(tenant)].search(
                text, vector, mode, k, ids, tenant
            )
        if mode != "dense":
            raise ValueError(
                "global lexical/hybrid ranking is unsupported; specify tenant"
            )
        tasks = [
            self.pool.submit(adapter.search, text, vector, mode, k, ids)
            for adapter in self.shards
        ]
        # Fail closed if any shard fails; never label a partial catalog as complete.
        hits = [hit for task in tasks for hit in task.result()]
        if any(not math.isfinite(hit["score"]) for hit in hits):
            raise RuntimeError("invalid shard distance")
        return heapq.nsmallest(k, hits, key=lambda hit: (hit["score"], int(hit["id"])))

    def rows(self):
        for shard, adapter in enumerate(self.shards):
            for row in adapter.rows():
                if shard_for(row["tenant"]) != shard:
                    raise RuntimeError("document is stored on the wrong shard")
                yield row

    def stats(self):
        return {
            url: value
            for adapter in self.shards
            for url, value in adapter.stats().items()
        }

    def verify_replicas(self, fixture, versions=None, attempted=None):
        results = {}
        # Each view spans exactly one physical copy of each shard. The full-value
        # oracle detects missing/duplicate IDs; rows() separately rejects wrong placement.
        for replica in range(3):
            view = RoutedAdapter.__new__(RoutedAdapter)
            view.shards = [
                Adapter(fixture, [adapter.http.urls[replica]])
                for adapter in self.shards
            ]
            deadline = time.monotonic() + 120
            while True:
                try:
                    results[f"copy-{replica}"] = oracle(
                        view, fixture, versions, attempted
                    )
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
            for adapter in view.shards:
                adapter.http.client.close()
        return results

    def strength_test(self):
        results = {
            f"shard-{i}": adapter.strength_test()
            for i, adapter in enumerate(self.shards)
        }
        # Check ALL customers in every native search mode, including customers that
        # share a shard. Filter enforcement still matters after routing.
        checked = 0
        for tenant in sorted({row["tenant"] for row in self.fixture.products}):
            for mode in ["lexical", "dense", "hybrid"]:
                hits = self.search("*", self.fixture.qvectors[0], mode, tenant=tenant)
                if len(hits) != self.fixture.k or any(
                    self.fixture.products[int(hit["id"])]["tenant"] != tenant
                    for hit in hits
                ):
                    raise RuntimeError("tenant query leaked or under-returned")
                checked += 1
        return {"native_checks": results, "tenant_isolation_queries": checked}


def tenant_benchmark(adapter, fixture):
    limit = min(parameter("BENCH_QUERIES", 1000, 10, 10000), len(fixture.queries))
    outputs = {}
    quality_limit = min(limit, parameter("QUALITY_QUERIES", 100, 1, 1000))
    tenants = sorted({row["tenant"] for row in fixture.products})
    tenant_ids = {
        tenant: np.array(
            [i for i, row in enumerate(fixture.products) if row["tenant"] == tenant]
        )
        for tenant in tenants
    }
    for mode in ["lexical", "dense", "hybrid"]:
        for clients in [1, 8]:

            def one(i):
                tenant = tenants[i % len(tenants)]
                start = time.perf_counter()
                hits = adapter.search(
                    fixture.queries[i]["text"], fixture.qvectors[i], mode, tenant=tenant
                )
                elapsed = time.perf_counter() - start
                ids = [hit["id"] for hit in hits]
                if len(ids) != len(set(ids)) or any(
                    fixture.products[int(pid)]["tenant"] != tenant for pid in ids
                ):
                    raise RuntimeError("benchmark tenant leak or duplicate")
                if mode == "dense" and len(ids) != fixture.k:
                    raise RuntimeError("short tenant dense result")
                return elapsed, ids

            for i in range(min(16, limit)):
                one(i)
            start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
                measured = list(pool.map(one, range(limit)))
            seconds = time.perf_counter() - start
            samples = [elapsed for elapsed, _ in measured]
            output = summary(samples) | {
                "qps": limit / seconds,
                "seconds": seconds,
                "failures": 0,
                "latency_seconds": samples,
            }
            if mode == "dense":
                output["recall_at_k"] = statistics.mean(
                    recall(
                        fixture,
                        i,
                        measured[i][1],
                        tenant_ids[tenants[i % len(tenants)]],
                    )
                    for i in range(quality_limit)
                )
                output["recall_queries"] = quality_limit
            outputs[f"tenant-{mode}/clients-{clients}"] = output
    save(
        "tenant-benchmark",
        {
            "results": outputs,
            "scope": "tenant 1 + i % 100 for the default fixture, tenant-filtered top 10, precomputed vectors; 16 warmups per case",
            "fixture_sha256": fixture.manifest["sha256"],
        },
    )


def run():
    if sys.argv[1] == "routing-test":
        import unittest

        suite = unittest.defaultTestLoader.discover("/app", pattern="test_sharding.py")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
    elif sys.argv[1] == "benchmark":
        fixture = Fixture()
        adapter = RoutedAdapter(fixture)
        benchmark(adapter, fixture)
        tenant_benchmark(adapter, fixture)
    else:
        main(RoutedAdapter)
