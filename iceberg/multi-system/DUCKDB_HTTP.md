# DuckDB application over ClickHouse HTTP

The active `make duckdb-clickhouse` workflow now uses **DuckDB 2.0.1 core
`httpfs` reads plus Query.Farm `http_client` 9bf6584a9d writes**. It does not load
`clickhouse_scanner`. The old native connector remains available as
`make duckdb-clickhouse-native` and its explicit regression/fault targets;
their results do not qualify the new HTTP fault behavior.

After the README's five-command infrastructure setup, additional commands are:

```sh
make duckdb-clickhouse-http-read ROWS=2000000
make duckdb-clickhouse-http-hot ROWS=2000000
make duckdb-clickhouse ROWS=2000000
```

The last command needs enough free native storage slots for a new Iceberg table.
The current retained fixture has **one free slot; the unchanged guard requires
two**. Its actual full entry-point preflight rejected before application release
or DDL. Do not delete old evidence/data or lower the guard to force a pass.
The HTTP hot write/read and read-only retained-Iceberg checks below were executed.
The fresh full publish/restore workflow is implemented but remains unqualified.

## Measured correctness

On 2026-11-23, actual Make runs produced:

| Check | Input | Result |
|---|---:|---|
| Generated ClickHouse rows, DuckDB one thread | 2,000,000 | Complete expected bodies; zero invalid |
| Same, two threads | 2,000,000 | Complete expected bodies; zero invalid |
| HTTP JSONColumns inserts | 20 × 100,000 | All 20 successful responses; 2,000,000 ACKed rows |
| HTTP hot read and independent native readback | 2,000,000 | Exact IDs, uniqueness, amounts and SHA256 bodies; zero invalid |
| Retained Iceberg via ClickHouse HTTP, both thread counts | 2,097,000 each | Complete expected bodies; catalog pointer unchanged |
| Fresh full Iceberg publish/restore | 2,000,000 planned | Capacity preflight blocked; not run |

The read-only cold command was:

```sh
HTTP_COLD_CASE=fe20a081-ab77-4c71-9ecb-c2332dce4a7e make duckdb-clickhouse-http-read ROWS=2097000
```

The cold check also passes after the final explicit HTTP limits and fresh-Iceberg-
metadata settings; its second raw trial is preserved separately. The resource
table below labels the first cold trial; both trials have complete counters in
the JSON. The hot trial predates those URL/cache settings; its hardened finalizer
was replayed offline, without claiming a second write trial.

That optional diagnostic uses the existing same-example durable witness and
checks its UUID, acknowledged intervals and native metadata pointer. It does not
seed a new cold table or require a sibling example. The default generated read
check needs no prior table.

All **250 controller tests** pass, including rejection of HTTP errors, an error
body despite HTTP 200, incomplete response witnesses and legacy-scanner SQL.
The finalizer was tightened after the hot run; saved real HTTP/ACK/body/version
files pass its offline replay. No second two-million-row write was claimed.
The compact [results](DUCKDB_HTTP_RESULTS.json) preserve the passed cases and the
initial failed runtime, actual capacity rejection, versions, resources and hashes.

## Interface and deployment

This is an explicit HTTP SQL interface, **not an ATTACH-compatible replacement**.
DuckDB `read_json` streams JSONEachRow with declared BIGINT/INTEGER/VARCHAR types;
the scoped HTTP secret targets only `http://clickhouse:8123/`. DuckDB
`http_post` sends JSONColumns batches generated locally inside DuckDB. Responses
are materialized once; status 200 and an empty mutation body are required before
an ACK file is written. Automatic retries and redirects are disabled. This is
not a transactional rollback or exactly-once contract; ambiguous-write and
fault/recovery tests still need porting. ACKs are fsynced during final verification,
so this healthy test does not establish power-loss-safe live journaling.

The local [Dockerfile](app/Dockerfile.http) derives from the Docker-built runner
created by this example's `make up`, installs the vendor-supported DuckDB 2.x
extension repository, verifies the extension binary SHA256 and keeps unsigned
extensions disabled. It builds a distinct image and leaves the legacy image intact.
The [Compose override](docker-compose.http.yml) retains the owned internal network
and 2 CPU / 2 GiB application cap. No host credentials or cloud services are used.
The current-image cleanup inventory includes the new image; existing preservation
and ownership gates still apply before teardown.

The first runtime failed because a host-UID override changed the process home,
preventing repository lookup; `ENV HOME=/home/app` fixed lookup of the image's
installed repository metadata without enabling unsigned code. That failure is
preserved. Compatibility preparation also found: native `chsql_native` was served
as a DuckDB 1.5.5 build and rejected by 2.0.1; `chsql` had no matching community
artifact; the community `http_client` stub directs 2.x users to Query.Farm.
The vendor repository's signed build loaded successfully. These observations are
specific to the tested platform/artifacts, not universal availability claims.

[HTTP extension documentation](https://query.farm/products/extensions/http_client/)
marks it experimental and documents synchronous requests and response buffering.
Inspected the installed revision's
[request implementation](https://github.com/Query-farm/httpclient/blob/9bf6584a9d/src/http_client_extension.cpp):
POST sends the supplied JSON body through DuckDB's HTTP layer; reads use core
httpfs instead of buffering two million rows in the scalar HTTP extension.
Signature verification is not evidence of production readiness.

## Resources and limits

Each two-million-row four-field dataset contains **168,000,000 logical field
bytes**: 2,000,000 × (8 + 4 + 8 + 64). This excludes offsets, framing and engine
overhead. Native part uncompressed counters use a different layer.

| Case / role | Whole-case CPU core-seconds | Mean CPU (one core = 100%) | Sampled charged memory peak MiB |
|---|---:|---:|---:|
| Generated reads / DuckDB | 1.522 | 21.68% | 369.74 |
| Generated reads / ClickHouse | 5.018 | 59.88% | 1,486.61 |
| Hot write/read / DuckDB | 1.466 | 19.94% | 496.12 |
| Hot write/read / ClickHouse | 3.451 | 40.51% | 1,296.14 |
| Cold reads / DuckDB | 1.460 | 23.70% | 50.03 |
| Cold reads / ClickHouse | 4.919 | 66.92% | 1,452.85 |

Storage CPU is 0.119 / 0.136 / 0.703 core-seconds respectively; sampled charged
peaks are 768.84 / 768.54 / 819.80 MiB. These unequal whole-case windows include
setup/probes and background work, not per-query costs or cross-connector speedups.
Client memory includes charged cache/kernel memory; lifetime peaks are separate.

The twenty 100k-row inserts report server elapsed **96.885 ms median**
(**80.181–115.519 ms**) in `X-ClickHouse-Summary`. These exclude DuckDB body
construction, client/network time and final reconciliation. There is no measured
end-to-end throughput or established tail percentile here.

Post-run native active parts contain 2M rows in **137,250,890 disk bytes**,
**137,177,513 compressed / 184,000,000 native uncompressed bytes (1.3413×)**.
Codec is the inherited server default; byte-level codec inspection is unavailable.
This measured native ratio is distinct from the 168M logical field-byte sum.
HTTP payloads have no explicitly requested compression; wire compression ratio
was not separately measured.

Hot-case ClickHouse allocated bytes change **2,827,718,656 → 3,080,069,120**
with a **3,092,041,728** sampled peak. CLI results-bind bytes change
**1,359,872 → 1,568,768**. VM device counters record **461,668,352 write bytes /
1,333 writes** over 8.52 s (54,186,426 bytes/s, 156.46 write IOPS), zero read
counters. Generated and cold read cases also record **414,601,216** and
**517,058,560** ClickHouse write bytes respectively. These include logs,
background work and possible HTTP response spooling; they are not automatically
data-write amplification caused by the query. Exact attribution remains open.
All per-role disk, CPU, memory and I/O counters are retained in the JSON.

CLI device I/O, physical disk latency and per-query resource attribution are
unavailable. Caches were not cleared; these are correctness trials on one host,
not sustained, repeated or equal-hardware performance comparisons. Full HTTP
publish/restore, type-edge cases, ambiguous writes, crashes, partitions, concurrent
compaction and repeated performance remain open. Old manifest-format and native
memory failures remain failed; this interface switch does not repair them.
