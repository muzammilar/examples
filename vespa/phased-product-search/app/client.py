import concurrent.futures
import io
import os
import sys
import time
import zipfile
from pathlib import Path
from workload import HTTP, META, main, oracle


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

        suite = unittest.defaultTestLoader.discover("/app", pattern="test_*.py")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
    elif sys.argv[1] in {"load", "benchmark", "stats"}:
        main(Adapter)
    else:
        raise ValueError("unsupported legacy operation; use the preserved-history controller")
