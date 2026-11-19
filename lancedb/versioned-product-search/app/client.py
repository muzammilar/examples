import json
import os
import sys
import hashlib
import threading
from workload import Fixture, META, benchmark
from workload import HTTP


class Adapter:
    modes = ["lexical", "dense", "hybrid"]

    def __init__(self, fixture):
        self.fixture = fixture
        self.http = HTTP(os.environ["URLS"].split(","))
        self.writer = HTTP(["http://writer:8080"])
        self.expected = None
        self.validated_hits = 0
        self.validation_lock = threading.Lock()
        if sys.argv[1:] == ["benchmark"]:
            # Full native/witness verification is a controller precondition. Freeze its
            # quiescent current bodies for validation of every timed search response.
            self.expected = {}
            digest = hashlib.sha256()
            for row in self.rows("http://writer:8080"):
                pid = row["id"]
                if pid in self.expected or row != fixture.row(int(pid), row["version"]):
                    raise RuntimeError("invalid quiescent benchmark snapshot")
                self.expected[pid] = row
            if len(self.expected) != len(fixture.products):
                raise RuntimeError("short benchmark snapshot")
            for pid in sorted(self.expected, key=int):
                digest.update(
                    (json.dumps(self.expected[pid], sort_keys=True) + "\n").encode()
                )
            self.expected_sha256 = digest.hexdigest()

    def search(self, text, vector, mode, k, ids=None, tenant=None):
        reply = self.http.request(
            "POST",
            "/search",
            read=True,
            json={
                "text": text,
                "vector": vector.tolist(),
                "mode": mode,
                "k": k,
                "ids": ids,
                "tenant": tenant,
            },
        ).json()
        if (
            type(reply.get("catalog_version")) is not int
            or reply["catalog_version"] < 1
        ):
            raise RuntimeError("missing search snapshot version")
        hits = reply["hits"]
        seen = set()
        for hit in hits:
            pid = hit["id"]
            if (
                pid in seen
                or not pid.isascii()
                or not pid.isdecimal()
                or str(int(pid)) != pid
                or not 0 <= int(pid) < len(self.fixture.products)
            ):
                raise RuntimeError("bad/duplicate search ID")
            seen.add(pid)
            expected = (
                self.expected[pid]
                if self.expected is not None
                else self.fixture.row(int(pid), hit["version"])
            )
            if any(
                k not in hit
                or type(hit[k]) is not type(expected[k])
                or hit[k] != expected[k]
                for k in META
            ):
                raise RuntimeError("wrong/stale complete search body")
            if (
                tenant is not None
                and hit["tenant"] != tenant
                or ids is not None
                and pid not in ids
            ):
                raise RuntimeError("filter leaked")
        if mode in ("dense", "hybrid"):
            eligible = len(self.fixture.products) if ids is None else len(set(ids))
            if tenant is not None:
                eligible = sum(
                    p["tenant"] == tenant
                    for p in self.fixture.products
                    if ids is None or p["id"] in ids
                )
            if len(hits) != min(k, eligible):
                raise RuntimeError("short dense/hybrid response")
        with self.validation_lock:
            self.validated_hits += len(hits)
        return hits

    def rows(self, url=None):
        with self.http.client.stream(
            "GET", (url or self.http.urls[0]) + "/rows"
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    yield json.loads(line)

    def stats(self):
        result = {}
        for url in dict.fromkeys(["http://writer:8080"] + self.http.urls):
            try:
                result[url] = self.http.request(
                    "POST", "/stats", url=url, read=True, json={}
                ).json()
            except Exception as error:
                result[url] = {"error": str(error)}
        result["response_validation"] = {
            "validated_hits": self.validated_hits,
            "scope": "all returned metadata fields and dense/hybrid counts; not exact ANN ranking",
            "quiescent_snapshot_rows": len(self.expected)
            if self.expected is not None
            else None,
            "quiescent_snapshot_sha256": getattr(self, "expected_sha256", None),
            "timed_boundary": "HTTP, JSON decoding and complete body/version validation",
        }
        return result

    def strength_test(self):
        result = self.writer.request("POST", "/strength", json={}).json()
        if os.environ["MODE"] == "cluster":
            response = self.http.client.post(
                self.http.urls[0] + "/update", json={"rows": []}
            )
            if response.status_code != 403:
                raise RuntimeError("read-only worker accepted mutation")
            result["reader_write_rejected"] = True
        return result


if __name__ == "__main__":
    if sys.argv[1:] != ["benchmark"]:
        raise SystemExit(
            "this entry point runs benchmark; use Make for lifecycle/recovery"
        )
    fixture = Fixture()
    benchmark(Adapter(fixture), fixture)
