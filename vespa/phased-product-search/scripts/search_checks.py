"""Search quality and tenant isolation stay separate from full local-copy proof."""

import json
from pathlib import Path
import statistics

from client import Adapter
from workload import Fixture, recall, save

ROOT = Path("/results")


def check():
    fixture = Fixture()
    adapter = Adapter(fixture)
    quality = []
    queries = 0
    for index, query in enumerate(fixture.queries[:50]):
        for mode in adapter.modes:
            hits = adapter.search(query["text"], fixture.qvectors[index], mode, k=10)
            ids = [str(hit["id"]) for hit in hits]
            if len(ids) != len(set(ids)) or any(
                not 0 <= int(key) < len(fixture.products) for key in ids
            ):
                raise RuntimeError("search returned invalid or duplicate IDs")
            if mode == "dense":
                if len(ids) != 10:
                    raise RuntimeError(
                        "dense global search returned fewer than ten products"
                    )
                quality.append(recall(fixture, index, ids))
            queries += 1
    for tenant in range(1, 101):
        index = (tenant - 1) % len(fixture.queries)
        eligible = sum(row["tenant"] == tenant for row in fixture.products)
        for mode in ["lexical", "dense", "hybrid"]:
            hits = adapter.search(
                fixture.queries[index]["text"],
                fixture.qvectors[index],
                mode,
                k=10,
                tenant=tenant,
            )
            ids = [str(hit["id"]) for hit in hits]
            if len(ids) != len(set(ids)) or any(
                fixture.products[int(key)]["tenant"] != tenant for key in ids
            ):
                raise RuntimeError("tenant filter leaked or duplicated a product")
            if mode == "dense" and len(ids) != min(10, eligible):
                raise RuntimeError("tenant-filtered dense search under-returned")
            queries += 1
    mean = statistics.mean(quality)
    minimum = 0.90
    reference = ROOT / "quality-baseline.json"
    if reference.exists():
        baseline = json.loads(reference.read_text())
        if baseline["fixture_sha256"] != fixture.manifest["sha256"]:
            raise RuntimeError("search reference belongs to another corpus")
        minimum = max(minimum, baseline["mean_dense_recall_at_10"] - 0.02)
    if mean < minimum:
        raise RuntimeError(
            f"dense recall {mean} is below the example's {minimum} quality floor"
        )
    result = {
        "queries": queries,
        "tenants": 100,
        "mean_dense_recall_at_10": mean,
        "minimum_recall": minimum,
        "tenant_leaks": 0,
        "duplicate_results": 0,
        "strength": adapter.strength_test(),
        "fixture_sha256": fixture.manifest["sha256"],
    }
    if not reference.exists():
        reference.write_text(json.dumps(result, indent=2) + "\n")
    save("correctness", result)


if __name__ == "__main__":
    check()
