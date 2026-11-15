# ESCI product-search fixture

Shared, immutable real-text input for [Typesense](../../typesense/product-search),
[pgvector](../../postgres/pgvector-product-search),
[LanceDB](../../lancedb/versioned-product-search) and
[Vespa](../../vespa/phased-product-search). Database servers are independent;
only corpus preparation and the evaluation contract are shared.

## Preparation

`make download prepare` verifies the pinned Amazon Parquet payloads, generates
real embeddings, and writes a checksum manifest. Docker Compose examples invoke
this automatically. Host requirements: Docker, Make, Python 3.11+ and internet on the
first run. Dependencies and model execution run in a pinned Python Docker image.
`make clean-image` removes only the preparation image. Download/model/fixture
caches are gitignored and intentionally retained across example teardown.

| Parameter | Default / observed |
|---|---:|
| Source revision | `7916cdf6ab75a462e77f20ab40428a10923998d5` |
| Verified products Parquet | 1,108,857,465 bytes |
| Verified examples Parquet | 51,286,808 bytes |
| Source US products | 1,215,854 |
| Selected products | 100,000 |
| Test queries | 1,000 |
| Required graded candidate products | 20,103 |
| Synthetic tenants | 100 |
| Seed | 20261115 |
| Dimensions / dtype | 384 / normalized float32 |
| Text token limit | 128 |
| Preparation + exhaustive-reference time | 1,332.91 s |

Measured 2026-11-15, Apple M4 Pro / Docker VM aarch64, preparation capped at
4 CPUs / 8 GiB. The timing includes embedding and exhaustive reference, excludes
initial Parquet/model download, and overlapped small integration checks; it is
setup timing, not an isolated encoder benchmark.

`DOCUMENTS`, `QUERIES`, `SEED` are Make variables. A completed fixture is reused
only if parameters and every recorded artifact hash match. A mismatch fails;
explicitly move/remove this example-owned `prepared/` directory before changing
parameters. Insufficient capacity for all graded candidates also fails. Never
duplicate products to inflate size.

Query IDs are selected by a seeded SHA-256 ordering from US, small-version,
**test** queries. All their graded candidates are retained; seeded distractors
fill the catalog. Product identity is `(product_locale, product_id)`, then mapped
to deterministic dense numeric IDs for cross-engine checks. This is a coverage-
preserving subset, not a uniform/full-ESCI leaderboard submission. No relevance
parameters were tuned using this test fixture. Repeated text across upstream
train/test splits is not audited; do not infer leakage-free model evaluation.

## Encoder

FastEmbed **0.9.1** uses the quantized ONNX model
`Qdrant/bge-small-en-v1.5-onnx-Q` at revision
`aa8f8b060edb00e03bfdd08813a2949946c8ba55` through its maintained BGE adapter.
Products concatenate title, brand and description; description is limited to
4,000 characters and the combined input to 1,800 characters / 128 tokens.
Queries use `query_embed`; all output vectors are L2-normalized. NumPy 2.5.4,
DuckDB 1.5.5 and PyArrow 26.0.1 handle arrays/Parquet. Model artifact hashes,
preprocessing parameters and fixture hashes are recorded in
`prepared/manifest.json`. The committed `measured-fixture.json` records the exact
input hashes used for these results; it is a measurement record, not a substitute
for download verification. Engines never download models during a query benchmark.

## Evaluation contract

- **Storage:** complete ID set, no duplicates, all metadata values, synthetic
  tenant/stock/price constraints, and acknowledged versions. Corruption fails.
- **ANN recall@10:** independent exhaustive cosine/dot reference over identical
  normalized vectors; filtered queries use the exact eligible subset. Equal
  boundary distances count as ties. This measures index approximation, not
  semantic shopping relevance.
- **Candidate nDCG@10:** official ESCI candidate lists only; E=1, S=0.1, C=0.01,
  I=0, gain/log2(rank+2). Zero-IDCG queries are excluded and counted. Unjudged
  open-catalog results are not known negatives. This is not exhaustive precision.
- **Timing:** 1 and 8 clients, 16 warmups per case, 1,000 timed queries per
  retrieval mode by default. p50/p95/p99, QPS and dense recall are recorded.
  Warm request timings include client serialization/networking and exclude
  embedding. No cold-cache or end-to-end encoder latency is claimed.
- **Crash recovery:** attempted/acknowledged version journals are fsynced outside
  the database; ambiguous mutations are not replayed. Search continues under
  writes. Fault/client timestamps use the same Docker Linux monotonic clock.
  Recovery must preserve every acknowledged version; accepted ambiguous writes
  are allowed, invented/unattempted versions and wrong payloads are rejected.

The standard-library host runner owns only its Compose project and fault client.
HTTPX supplies bounded pooled HTTP, Psycopg supplies PostgreSQL pools/COPY, and
LanceDB supplies indexing/version operations. Seven negative contract tests cover
missing ACK versions, duplicate/corrupt data, safe read failover, mutation replay
prevention, ranking math, Unicode separators inside JSON strings and failure
timing that excludes successful writes before the interruption. JSONL
records are framed on LF, preserving those characters inside descriptions. No hand-written encoder, Parquet reader or vector
index is used.

## Verified runs

[`verified-runs.json`](verified-runs.json) records the audited **10 implemented
profiles**, **68,000 timed benchmark requests**, and **9 fault runs** across the
four examples, with input hashes, caps, numerical results and hashes of local raw
reports. Raw reports remain in ignored `results/` directories; their hashes are
provenance records, not a replacement for rerunning the tests. The record explicitly
distinguishes the tested fault targets from unrun optional host permutations.

## Files and licensing

```text
manifest.json          pinned source payload hashes/sizes
measured-fixture.json  exact input fingerprint for reported results
app/download.py        verify downloads before promotion
app/prepare.py         sampling, model inference, exact top-10
client/workload.py     common correctness/quality/timing contract
client/fault.py        scoped host-side crash orchestration
client/tests/          negative contract tests
Dockerfile / Makefile
cache/                 ignored raw data and model
prepared/              ignored immutable generated artifacts
```

[Amazon ESCI](https://github.com/amazon-science/esci-data) declares Apache-2.0;
the pinned upstream LICENSE and README/citation are retained and hash-verified
in `cache/`. Retain them with redistributed artifacts. The pinned
[LFS metadata](https://github.com/amazon-science/esci-data/tree/7916cdf6ab75a462e77f20ab40428a10923998d5/shopping_queries_dataset)
provides the complete payload SHA-256/lengths in `manifest.json`; both payloads
were downloaded and verified. Raw data and model files are not committed.
[BGE model card](https://huggingface.co/BAAI/bge-small-en-v1.5),
[pinned model](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q/tree/aa8f8b060edb00e03bfdd08813a2949946c8ba55),
[FastEmbed source](https://github.com/qdrant/fastembed/tree/v0.9.1).
