import json
import os
from workload import HTTP, main, oracle


class Adapter:
    modes = ["lexical", "dense", "hybrid"]

    def __init__(self, fixture):
        self.fixture = fixture
        self.http = HTTP(os.environ["URLS"].split(","))
        self.writer = HTTP(["http://writer:8080"])

    def load(self):
        return self.writer.request("POST", "/load", json={}).json()

    def update(self, rows):
        self.writer.request("POST", "/update", json={"rows": rows})

    def search(self, text, vector, mode, k, ids=None, tenant=None):
        return self.http.request(
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
        for url in ["http://writer:8080"] + self.http.urls:
            try:
                result[url] = self.http.request(
                    "POST", "/stats", url=url, read=True, json={}
                ).json()
            except Exception as error:
                result[url] = {"error": str(error)}
        return result

    def verify_replicas(self, fixture, minimum_versions=None, allow_attempted=None):
        class Reader:
            def __init__(self, parent, url):
                self.parent = parent
                self.url = url

            def rows(self):
                return self.parent.rows(self.url)

        return {
            url: oracle(Reader(self, url), fixture, minimum_versions, allow_attempted)
            for url in self.http.urls
        }

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

    def maintenance(self):
        return self.writer.request("POST", "/maintenance", json={}).json()


if __name__ == "__main__":
    main(Adapter)
