"""Prepare deterministic real-text retrieval artifacts using DuckDB, FastEmbed and NumPy."""

import hashlib
import json
import os
from pathlib import Path
import time

import duckdb
from fastembed import TextEmbedding
from huggingface_hub import snapshot_download
import numpy as np

OUT = Path("/prepared")
CACHE = Path("/cache")
MODEL = "Qdrant/bge-small-en-v1.5-onnx-Q"
REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
DIM = 384


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    n = int(os.environ.get("DOCUMENTS", "100000"))
    q = int(os.environ.get("QUERIES", "1000"))
    seed = int(os.environ.get("SEED", "20261115"))
    if not 1000 <= n <= 2000000 or not 10 <= q <= 10000:
        raise ValueError("DOCUMENTS=1000..2000000; QUERIES=10..10000")
    fingerprint = {
        "documents": n,
        "queries": q,
        "seed": seed,
        "model": MODEL,
        "revision": REVISION,
        "dimension": DIM,
        "max_tokens": 128,
        "description_chars": 4000,
        "schema_version": 2,
    }
    if (OUT / "manifest.json").exists():
        prior = json.loads((OUT / "manifest.json").read_text())
        if prior["parameters"] == fingerprint and all(
            digest(OUT / name) == sha for name, sha in prior["sha256"].items()
        ):
            print("Prepared fixture already verified", flush=True)
            return
        raise RuntimeError(
            "different/corrupt prepared fixture; use another output directory or explicitly remove this owned fixture"
        )
    source = json.loads(Path("/work/manifest.json").read_text())
    for artifact in source["artifacts"]:
        path = CACHE / Path(artifact["path"]).name
        if (
            path.stat().st_size != artifact["bytes"]
            or digest(path) != artifact["sha256"]
        ):
            raise RuntimeError("unverified source " + path.name)
    d = duckdb.connect()
    d.execute("SET threads=4")
    d.execute("SET memory_limit='4GB'")
    d.execute("SET temp_directory='/cache/duck-temp'")
    d.execute(
        "CREATE VIEW examples AS SELECT * FROM read_parquet('/cache/shopping_queries_dataset_examples.parquet')"
    )
    d.execute(
        "CREATE VIEW raw_products AS SELECT * FROM read_parquet('/cache/shopping_queries_dataset_products.parquet') WHERE product_locale='us'"
    )
    duplicates = d.execute(
        "SELECT count(*) FROM (SELECT product_id,count(*) n FROM raw_products GROUP BY product_id HAVING n!=1)"
    ).fetchone()[0]
    if duplicates:
        raise RuntimeError("duplicate source locale/product identity")
    d.execute(
        f"CREATE TABLE chosen_queries AS SELECT query_id,any_value(query) query FROM examples WHERE product_locale='us' AND small_version=1 AND split='test' GROUP BY query_id ORDER BY sha256(concat('{seed}',':',query_id)) LIMIT {q}"
    )
    d.execute(
        "CREATE TABLE needed AS SELECT DISTINCT product_id FROM examples JOIN chosen_queries USING(query_id) WHERE product_locale='us' AND small_version=1 AND split='test'"
    )
    required = d.execute("SELECT count(*) FROM needed").fetchone()[0]
    if required > n:
        raise RuntimeError(f"{required} judged products exceed DOCUMENTS={n}")
    d.execute(
        f"CREATE TABLE picked AS SELECT product_id FROM needed UNION ALL (SELECT product_id FROM raw_products WHERE product_id NOT IN (SELECT product_id FROM needed) ORDER BY sha256(concat('{seed}',':',product_id)) LIMIT {n - required})"
    )
    d.execute(
        "CREATE TABLE selected AS SELECT p.* FROM raw_products p JOIN picked USING(product_id)"
    )
    if d.execute("SELECT count(*) FROM selected").fetchone()[0] != n:
        raise RuntimeError("not enough distinct products")
    rows = d.execute(
        "SELECT product_id,product_title,product_description,product_brand,product_color FROM selected ORDER BY product_id"
    ).fetchall()
    products = []
    ids = {}
    for i, (pid, title, description, brand, color) in enumerate(rows):
        h = int.from_bytes(
            hashlib.sha256(f"{seed}:us:{pid}".encode()).digest()[:8], "little"
        )
        product = {
            "id": str(i),
            "source_id": pid,
            "locale": "us",
            "name": title or "",
            "description": (description or "")[:4000],
            "brand": brand or "_missing",
            "color": color or "_missing",
            "tenant": int(h % 100) + 1,
            "stock": int((h >> 8) % 100),
            "price_cents": int((h >> 16) % 100000) + 100,
            "version": 0,
        }
        products.append(product)
        ids[pid] = i
    judgments = {}
    for qid, pid, label in d.execute(
        "SELECT e.query_id,e.product_id,e.esci_label FROM examples e JOIN chosen_queries q USING(query_id) WHERE e.product_locale='us' AND e.small_version=1 AND e.split='test'"
    ).fetchall():
        judgments.setdefault(qid, {})[str(ids[pid])] = label
    queries = [
        {"id": int(qid), "text": text, "judged": judgments[qid]}
        for qid, text in d.execute(
            "SELECT query_id,query FROM chosen_queries ORDER BY query_id"
        ).fetchall()
    ]
    model_dir = CACHE / "model"
    snapshot_download(
        MODEL,
        revision=REVISION,
        local_dir=model_dir,
        allow_patterns=["*.json", "*.txt", "*.onnx"],
    )
    embedder = TextEmbedding(
        model_name="BAAI/bge-small-en-v1.5",
        specific_model_path=str(model_dir),
        local_files_only=True,
        threads=4,
    )
    embedder.model.tokenizer.enable_truncation(max_length=128)
    start = time.monotonic()
    vectors = np.lib.format.open_memmap(
        OUT / "vectors.npy", mode="w+", dtype=np.float32, shape=(n, DIM)
    )
    for lo in range(0, n, 256):
        texts = [
            (p["name"] + " " + p["brand"] + " " + p["description"])[:1800]
            for p in products[lo : lo + 256]
        ]
        vectors[lo : lo + len(texts)] = np.array(
            list(embedder.embed(texts, batch_size=64)), dtype=np.float32
        )
        if lo % 2560 == 0:
            print(f"Embedded {lo}/{n}", flush=True)
    vectors[:] /= np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors.flush()
    qvectors = np.array(
        list(embedder.query_embed([x["text"] for x in queries], batch_size=64)),
        dtype=np.float32,
    )
    qvectors /= np.linalg.norm(qvectors, axis=1, keepdims=True)
    np.save(OUT / "query_vectors.npy", qvectors)
    vectors.tofile(OUT / "vectors.f32")
    qvectors.tofile(OUT / "query_vectors.f32")
    with (OUT / "products.jsonl").open("w") as f:
        for p in products:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with (OUT / "queries.jsonl").open("w") as f:
        for x in queries:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    # NumPy/BLAS provides an independent exhaustive reference; engine ANN is not the oracle.
    exact = []
    for lo in range(0, q, 32):
        scores = qvectors[lo : lo + 32] @ vectors.T
        for score in scores:
            best = np.argpartition(-score, 9)[:10]
            best = best[np.lexsort((best, -score[best]))]
            exact.append(
                {
                    "ids": [int(i) for i in best],
                    "scores": [float(score[i]) for i in best],
                }
            )
    write_json(OUT / "exact.json", exact)
    files = [
        "products.jsonl",
        "queries.jsonl",
        "vectors.f32",
        "query_vectors.f32",
        "vectors.npy",
        "query_vectors.npy",
        "exact.json",
    ]
    write_json(
        OUT / "manifest.json",
        {
            "parameters": fingerprint,
            "required_judged_products": required,
            "source_products": d.execute(
                "SELECT count(*) FROM raw_products"
            ).fetchone()[0],
            "embedding_and_reference_seconds": time.monotonic() - start,
            "model_files": {
                p.name: digest(p) for p in model_dir.iterdir() if p.is_file()
            },
            "sha256": {name: digest(OUT / name) for name in files},
        },
    )
    print("Prepared complete fixture", flush=True)


if __name__ == "__main__":
    main()
