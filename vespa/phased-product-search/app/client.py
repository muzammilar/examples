import concurrent.futures
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from workload import HTTP, META, Fixture, main, oracle, read_records, save
from native import read_copy


class Adapter:
    modes = ["lexical", "dense", "hybrid", "rerank"]

    def __init__(self, fixture):
        self.fixture = fixture
        self.urls = os.environ["URLS"].split(",")
        self.http = HTTP(self.urls)
        self.storage_urls = (
            [f"http://content{i}:19092" for i in range(9)]
            if os.environ.get("MODE") == "sharded"
            else self.urls
        )

    def deploy(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for file in Path("/application").rglob("*"):
                if file.is_file():
                    z.write(file, str(file.relative_to("/application")))
        response = self.http.client.post(
            "http://node0:19071/application/v2/tenant/default/prepareandactivate",
            content=archive.getvalue(),
            headers={"Content-Type": "application/zip"},
            timeout=180,
        )
        print("Deployment:", response.text, flush=True)
        response.raise_for_status()
        deadline = time.monotonic() + 240
        while True:
            try:
                for url in self.urls:
                    r = self.http.client.get(url + "/state/v1/health", timeout=3)
                    r.raise_for_status()
                    if r.json().get("status", {}).get("code") != "up":
                        raise RuntimeError("container not ready")
                    probe = self.http.client.post(
                        url + "/search/",
                        json={
                            "yql": "select pid from product where true",
                            "hits": 0,
                            "ranking": "lexical",
                            "timeout": "2s",
                        },
                        timeout=5,
                    )
                    probe.raise_for_status()
                    root = probe.json().get("root", {})
                    if (
                        root.get("errors")
                        or root.get("coverage", {}).get("coverage", 0) < 100
                    ):
                        raise RuntimeError(
                            "content/query coverage not ready: " + str(root)
                        )
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)

    def feed(self, row):
        fields = {k: v for k, v in row.items() if k != "id"}
        fields["pid"] = int(row["id"])
        fields["embedding"] = {"values": self.fixture.vectors[int(row["id"])].tolist()}
        self.http.request(
            "POST",
            "/document/v1/catalog/product/docid/" + row["id"],
            json={"fields": fields},
        )

    def load(self):
        self.deploy()
        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for lo in range(0, len(self.fixture.products), 1000):
                for _ in pool.map(self.feed, self.fixture.products[lo : lo + 1000]):
                    pass
                if lo % 10000 == 0:
                    print(
                        f"Fed {min(lo + 1000, len(self.fixture.products))}/{len(self.fixture.products)}",
                        flush=True,
                    )
        self.wait_rows()
        return {
            "feed_seconds": time.perf_counter() - start,
            "documents": len(self.fixture.products),
        }

    def update(self, rows):
        endpoint = None
        for url in self.urls:
            try:
                if self.http.client.get(url + "/state/v1/health", timeout=2).is_success:
                    endpoint = url
                    break
            except Exception:
                pass
        if endpoint is None:
            raise RuntimeError("no healthy feed endpoint; no mutation sent")
        for row in rows:
            self.http.request(
                "PUT",
                "/document/v1/catalog/product/docid/" + row["id"],
                url=endpoint,
                params={"timeout": "3s"},
                timeout=5,
                json={
                    "fields": {
                        k: {"assign": row[k]}
                        for k in ["stock", "price_cents", "version"]
                    }
                },
            )

    def search(self, text, vector, mode, k, ids=None, tenant=None):
        nearest = "({targetHits:100,hnsw.exploreAdditionalHits:256}nearestNeighbor(embedding,q))"
        lexical = "userInput(@text)"
        expression = (
            lexical
            if mode == "lexical"
            else nearest
            if mode == "dense"
            else "(" + nearest + " OR " + lexical + ")"
        )
        if ids is not None:
            expression += " AND pid in (" + ",".join(str(int(i)) for i in ids) + ")"
        if tenant is not None:
            expression += " AND tenant = " + str(int(tenant))
        body = {
            "yql": "select * from product where " + expression,
            "text": text,
            "ranking": mode,
            "hits": k,
            "timeout": "10s",
            "presentation.summary": "metadata",
        }
        if mode != "lexical":
            body["input.query(q)"] = {"values": vector.tolist()}
        result = self.http.request("POST", "/search/", read=True, json=body).json()
        if result.get("root", {}).get("errors"):
            raise RuntimeError(str(result["root"]["errors"]))
        coverage = result.get("root", {}).get("coverage", {})
        if coverage.get("coverage", 100) < 100 or not coverage.get("full", True):
            raise RuntimeError("incomplete search coverage: " + str(coverage))
        return [
            self.normalize(x["fields"])
            for x in result.get("root", {}).get("children", [])
            if "fields" in x and "pid" in x["fields"]
        ]

    def normalize(self, row):
        row = row.copy()
        row["id"] = str(row.pop("pid"))
        return {
            k: row.get(
                k, 0 if k in ["tenant", "stock", "price_cents", "version"] else ""
            )
            for k in META
        }

    def rows(self):
        params = {
            "cluster": "catalog",
            "selection": "true",
            "fieldSet": "product:" + ",".join("pid" if k == "id" else k for k in META),
            "wantedDocumentCount": 1000,
        }
        while True:
            data = self.http.request("GET", "/document/v1/", params=params).json()
            for doc in data.get("documents", []):
                row = self.normalize(doc["fields"])
                if doc["id"] != "id:catalog:product::" + row["id"]:
                    raise RuntimeError("document URI and payload ID disagree")
                yield row
            if not data.get("continuation"):
                break
            params["continuation"] = data["continuation"]

    def wait_rows(self):
        deadline = time.monotonic() + 180
        while True:
            try:
                oracle(self, self.fixture)
                return
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)

    def stats(self):
        stats = {}
        for url in self.storage_urls:
            try:
                host = url.split(":")[1].strip("/")
                metrics = self.http.client.get(
                    "http://" + host + ":19092/metrics/v2/values", timeout=10
                )
                metrics.raise_for_status()
                data = metrics.json()

                def selected(value):
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if isinstance(item, (int, float)) and any(
                                x in key
                                for x in [
                                    "document",
                                    "bucket",
                                    "memory_usage",
                                    "feed.latency",
                                ]
                            ):
                                yield key, item
                            else:
                                yield from selected(item)
                    elif isinstance(value, list):
                        for item in value:
                            yield from selected(item)

                stats[url] = {"metrics": dict(selected(data))}
            except Exception as error:
                stats[url] = {"error": str(error)}
        return stats

    def verify_replicas(self, fixture, versions=None, attempted=None):
        deadline = time.monotonic() + 300
        while True:
            stats = self.stats()
            counts = {}
            for url, value in stats.items():
                metrics = value.get("metrics", {})
                counts[url] = {
                    key: metrics.get(
                        "content.proton.documentdb.documents." + key + ".last"
                    )
                    for key in ["total", "ready", "active"]
                }
            expected = len(fixture.products)
            if os.environ.get("MODE") == "sharded":
                values = list(counts.values())
                partial_nodes = all(
                    isinstance(row[key], (int, float)) and 0 < row[key] < expected
                    for row in values
                    for key in ["total", "ready"]
                )
                converged = partial_nodes and all(
                    sum(row[key] for row in values[group * 3 : group * 3 + 3])
                    == expected
                    for group in range(3)
                    for key in ["total", "ready"]
                )
            else:
                converged = all(
                    value["total"] == expected and value["ready"] == expected
                    for value in counts.values()
                )
            if converged:
                return {
                    "physical_document_counts": counts,
                    "logical_payload_oracle": oracle(
                        self, fixture, versions, attempted
                    ),
                    "scope": "per-node total/ready counts plus logical full-row visitor; not a local-file payload checksum of every replica",
                }
            if time.monotonic() > deadline:
                raise RuntimeError("replica counts not converged: " + str(counts))
            time.sleep(3)

    def expected_versions(self):
        versions = {}
        attempted = {}
        if os.environ["NATIVE_PHASE"] == "fault":
            for record in read_records(Path("/results/churn.jsonl")):
                if record["stage"] not in ("attempt", "ack"):
                    continue
                target = attempted if record["stage"] == "attempt" else versions
                for row in record["rows"]:
                    i = int(row["id"])
                    target[i] = max(target.get(i, 0), int(row["version"]))
        return versions, attempted

    def verify_sharded_copies(self):
        versions, attempted = self.expected_versions()
        phase = os.environ["NATIVE_PHASE"]
        n = len(self.fixture.products)
        counts = {}
        groups = {}
        parent = self

        class Group:
            def __init__(self, group):
                self.group = group

            def rows(self):
                for node in range(self.group * 3, self.group * 3 + 3):
                    documents = read_copy(
                        Path(f"/results/.native-{phase}-{node}.json"), n
                    )
                    counts[str(node)] = sum(
                        document is not None for document in documents
                    )
                    if not 0 < counts[str(node)] < n:
                        raise RuntimeError("node does not hold a proper catalog subset")
                    for document in documents:
                        if document is not None:
                            yield parent.normalize(document["fields"])

        for group in range(3):
            groups[str(group)] = oracle(Group(group), self.fixture, versions, attempted)
        save(
            "copy-" + phase + "-placement",
            {
                "groups": groups,
                "rows_per_node": counts,
                "stored_rows": sum(counts.values()),
                "directed_gets": n * len(counts),
                "absent_copies": n * len(counts) - sum(counts.values()),
                "logical_rows": n,
                "copies_per_document": 3,
                "scope": "native replica-directed get for every ID on all 9 content nodes; each group contains exactly one full metadata copy, vectors excluded",
            },
        )

    def verify_copy(self):
        versions, attempted = self.expected_versions()
        documents = json.loads(
            (Path("/results") / os.environ["NATIVE_FILE"]).read_text()
        )
        parent = self

        class Copy:
            def rows(self):
                for document in documents:
                    row = parent.normalize(document["fields"])
                    if document["id"] != "id:catalog:product::" + row["id"]:
                        raise RuntimeError(
                            "physical document URI and payload ID disagree"
                        )
                    yield row

        result = oracle(Copy(), self.fixture, versions, attempted)
        result["scope"] = (
            "all IDs and metadata read using native vespa-get --replica-override; vectors excluded"
        )
        result["node"] = int(os.environ["NATIVE_NODE"])
        save(
            "copy-" + os.environ["NATIVE_PHASE"] + "-" + os.environ["NATIVE_NODE"],
            result,
        )

    def strength_test(self):
        results = {}
        for mode in self.modes:
            q = self.fixture.queries[0]
            hits = self.search(q["text"], self.fixture.qvectors[0], mode, 10)
            results[mode] = {"returned": len(hits)}
        return {"rank_profiles": results, "global_rerank_count": 100}


if __name__ == "__main__":
    if sys.argv[1] == "copy-contract-test":
        import unittest

        suite = unittest.defaultTestLoader.discover("/app", pattern="test_native.py")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
    elif sys.argv[1] == "deploy":
        Adapter(Fixture()).deploy()
    elif sys.argv[1] == "verify-sharded-copies":
        Adapter(Fixture()).verify_sharded_copies()
    elif sys.argv[1] == "reset-fault":
        fixture = Fixture()
        adapter = Adapter(fixture)
        ids = sorted(
            {
                int(row["id"])
                for record in read_records(Path("/results/churn.jsonl"))
                if record["stage"] == "attempt"
                for row in record["rows"]
            }
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            batches = (
                [fixture.row(i) for i in ids[lo : lo + 10]]
                for lo in range(0, len(ids), 10)
            )
            for _ in pool.map(adapter.update, batches):
                pass
        save("reset-fault", {"reset_ids": len(ids), "oracle": oracle(adapter, fixture)})
    elif sys.argv[1] == "verify-copy":
        Adapter(Fixture()).verify_copy()
    else:
        main(Adapter)
