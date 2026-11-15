import json
import io
import os
import time

from workload import HTTP, META, main, oracle


class Adapter:
    modes = ["lexical", "dense", "hybrid"]

    def __init__(self, fixture, urls=None):
        self.fixture = fixture
        self.http = HTTP(
            urls or os.environ.get("URLS", "http://ts1:8108").split(","),
            {"X-TYPESENSE-API-KEY": "local-search-key"},
        )

    def load(self, products=None):
        response = self.http.client.delete(self.http.urls[0] + "/collections/products")
        if response.status_code not in (200, 404):
            response.raise_for_status()
        fields = [
            {
                "name": key,
                "type": "int32"
                if key in ["tenant", "stock", "price_cents", "version"]
                else "string",
                "facet": key in ["brand", "color", "locale", "tenant", "stock"],
            }
            for key in META
            if key != "id"
        ]
        fields.append(
            {
                "name": "embedding",
                "type": "float[]",
                "num_dim": 384,
                "hnsw_params": {"M": 16, "ef_construction": 128},
            }
        )
        self.http.request(
            "POST", "/collections", json={"name": "products", "fields": fields}
        )
        products = self.fixture.products if products is None else products
        for lo in range(0, len(products), 500):
            self.update(products[lo : lo + 500])
        return {
            "replicas": len(self.http.urls),
            "index": "HNSW M=16 ef_construction=128",
        }

    def update(self, rows):
        lines = []
        for row in rows:
            value = row.copy()
            value["embedding"] = self.fixture.vectors[int(row["id"])].tolist()
            lines.append(json.dumps(value))
        response = self.http.request(
            "POST",
            "/collections/products/documents/import",
            url=self.http.available(),
            params={"action": "upsert"},
            content="\n".join(lines),
            headers={"Content-Type": "text/plain"},
        )
        results = [json.loads(line) for line in response.text.split("\n") if line]
        if len(results) != len(rows) or any(not x.get("success") for x in results):
            raise RuntimeError("partial/rejected import: " + str(results[:3]))

    def search(self, text, vector, mode, k=10, ids=None, tenant=None):
        query = {
            "collection": "products",
            "q": text if mode != "dense" else "*",
            "query_by": "name,description,brand",
            "per_page": k,
            "exclude_fields": "embedding,description",
        }
        if mode != "lexical":
            query["vector_query"] = (
                "embedding:("
                + json.dumps(vector.tolist())
                + ", k:100, ef:256, alpha:0.5)"
            )
        predicates = []
        if ids is not None:
            predicates.append(
                "id:=[" + ",".join("`" + str(int(i)) + "`" for i in ids) + "]"
            )
        if tenant is not None:
            predicates.append("tenant:=" + str(int(tenant)))
        if predicates:
            query["filter_by"] = " && ".join(predicates)
        result = self.http.request(
            "POST", "/multi_search", read=True, json={"searches": [query]}
        ).json()["results"][0]
        if "error" in result:
            raise RuntimeError(str(result))
        return [
            {
                "id": x["document"]["id"],
                "score": x["vector_distance"]
                if mode == "dense"
                else x.get("vector_distance", 0),
            }
            for x in result["hits"]
        ]

    def rows(self):
        with self.http.client.stream(
            "GET",
            self.http.urls[0] + "/collections/products/documents/export",
            params={"exclude_fields": "embedding"},
        ) as response:
            response.raise_for_status()
            for line in io.BytesIO(response.read()):
                if line:
                    yield json.loads(line)

    def stats(self):
        result = {}
        for url in self.http.urls:
            try:
                result[url] = {
                    "debug": self.http.request("GET", "/debug", url=url).json(),
                    "collection": self.http.request(
                        "GET", "/collections/products", url=url
                    ).json(),
                }
            except Exception as e:
                result[url] = {"error": str(e)}
        return result

    def verify_replicas(self, fixture, versions=None, attempted=None):
        results = {}
        for url in self.http.urls:
            deadline = time.monotonic() + 120
            while True:
                try:
                    results[url] = oracle(
                        Adapter(fixture, [url]), fixture, versions, attempted
                    )
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
        return results

    def strength_test(self):
        base = self.http.urls[0]
        self.http.client.delete(base + "/collections/curated")
        self.http.request(
            "POST",
            "/collections",
            json={
                "name": "curated",
                "fields": [
                    {"name": "name", "type": "string"},
                    {"name": "brand", "type": "string", "facet": True},
                    {"name": "embedding", "type": "float[]", "num_dim": 384},
                ],
            },
        )
        try:
            docs = [
                {
                    "id": "a",
                    "name": "Studio headphones",
                    "brand": "A",
                    "embedding": [1.0] + [0.0] * 383,
                },
                {
                    "id": "b",
                    "name": "Studio headband",
                    "brand": "B",
                    "embedding": [0.0, 1.0] + [0.0] * 382,
                },
                {
                    "id": "bad",
                    "name": "Broken vector",
                    "brand": "B",
                    "embedding": [1.0, 2.0],
                },
            ]
            response = self.http.request(
                "POST",
                "/collections/curated/documents/import",
                content="\n".join(json.dumps(x) for x in docs),
                headers={"Content-Type": "text/plain"},
            )
            results = [json.loads(x) for x in response.text.split("\n") if x]
            if [x["success"] for x in results] != [True, True, False]:
                raise RuntimeError("import rejection gate failed")
            deadline = time.monotonic() + 30
            while True:
                visible = []
                for url in self.http.urls:
                    response = self.http.client.get(url + "/collections/curated")
                    if response.status_code == 404:
                        visible.append(None)
                    else:
                        response.raise_for_status()
                        visible.append(response.json()["num_documents"])
                if all(count == 2 for count in visible):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "curated replica visibility timeout: " + str(visible)
                    )
                time.sleep(0.1)
            for query in ["headphnes", "headph"]:
                result = self.http.request(
                    "GET",
                    "/collections/curated/documents/search",
                    params={
                        "q": query,
                        "query_by": "name",
                        "filter_by": "brand:=A",
                        "facet_by": "brand",
                    },
                ).json()
                if result["found"] != 1 or result["hits"][0]["document"]["id"] != "a":
                    raise RuntimeError(
                        "curated typo/prefix/filter case failed: "
                        + query
                        + " "
                        + str(result)
                    )
                facets = {
                    entry["value"]: entry["count"]
                    for facet in result.get("facet_counts", [])
                    if facet["field_name"] == "brand"
                    for entry in facet["counts"]
                }
                if facets != {"A": 1}:
                    raise RuntimeError("curated facet counts incorrect: " + str(facets))
            return {
                "facet_queries": 2,
                "rejected_bad_vector": True,
                "typo_and_prefix_queries": 2,
                "eligible_hits_each": 1,
            }
        finally:
            self.http.request("DELETE", "/collections/curated")


if __name__ == "__main__":
    if os.environ.get("MODE") == "sharded":
        from sharded import run

        run()
    else:
        main(Adapter)
