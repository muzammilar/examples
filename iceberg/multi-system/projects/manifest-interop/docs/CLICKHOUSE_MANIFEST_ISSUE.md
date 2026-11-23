# ClickHouse Iceberg appends omit the required manifest `content` header

Draft upstream report, reproduced locally on 2026-11-22. Not posted externally.

ClickHouse 26.10.10.3 appends to a freshly created Iceberg v2 table successfully,
but the resulting Avro manifest lacks the required key-value metadata property
`content`. DuckDB 2.0.1 then rejects the table. A read-only inspection of the actual
manifest headers confirms the missing property; this is not inferred from the
reader error alone.

## Environment

- ClickHouse 26.10.10.3, native Linux ARM64; image ID
  `sha256:b0784219f1821b8a9918732b8e6728dbed9246e13949c46e40d7c2a084d9ef9d`.
- DuckDB 2.0.1 with pinned Iceberg extension `f979b5b28a`.
- SeaweedFS 4.54 (`5ef99c1d8`), local S3 and Iceberg REST catalog; image ID
  `sha256:4b42c212543a0acd4c0e675215ecdcaf62a186ef98def76c7e12701871222d15`.
- Independent header inspection: Spark 4.1.4 / Iceberg 1.12.0
  (`212aa894e919ff9fdf89160da4aadcbe6858ac01`).
- Isolated Compose project `iceberg-multi-system-current`, local lab credentials,
  no cloud storage. Both inspected tables report `format-version: 2`.

## Reproduction

From `iceberg/multi-system/` in this repository, on a fresh example-owned fixture:

```sh
make LAB_PROFILE=current docker-up
make LAB_PROFILE=current up
make LAB_PROFILE=current probe RESULTS_DIR=results/current/reproduce-clickhouse-manifest
```

**The probe drops and recreates its own `lab.probe` and `lab.reverse` tables.**
Do not rerun it against a fixture whose probe state must be preserved, or while
other experiments are using these tables. The commands above document reproduction;
they were not rerun while drafting this report.

The [probe source](../../../app/main.go) creates these unpartitioned tables through DuckDB:

```sql
CREATE TABLE lake.lab.probe
  (event_id BIGINT, customer_id INTEGER, amount_cents BIGINT);
CREATE TABLE lake.lab.reverse
  (event_id BIGINT, customer_id INTEGER, amount_cents BIGINT);
INSERT INTO lake.lab.probe
SELECT i, CAST(i % 100 AS INTEGER), i % 10000 + 1 FROM range(10000) t(i);
```

ClickHouse attaches the same REST catalog using `DataLakeCatalog`, with
`catalog_type='rest'`, `catalog_credential='demo:demo'`,
`vended_credentials=false`, `warehouse='warehouse'` and local
`storage_endpoint='http://storage:8333/warehouse'`. The probe enables
`allow_database_iceberg=1` and `allow_insert_into_iceberg=1`.

ClickHouse reads the DuckDB-written table successfully: 10,000 rows and
50,005,000 total `amount_cents`. It then writes the reverse-direction table:

```sql
INSERT INTO lake.`lab.reverse`
SELECT toInt64(number), CAST(number % 100 AS Int32),
       CAST(number % 10000 + 1 AS Int64)
FROM numbers(20000);
```

After detaching and opening a fresh DuckDB catalog connection, this query fails:

```sql
SELECT count(*), sum(amount_cents) FROM lake.lab.reverse;
```

Expected: 20,000 rows and 100,010,000 total `amount_cents`.
Actual:

```text
Invalid Configuration Error: Manifest 's3://warehouse/lab/reverse-e6584bac-3f70-4736-b39e-892ce0a5c11c/metadata/7d4f8876-a6b6-4df0-b759-1d373313ab58.avro' has invalid Avro key-value metadata, content is missing
```

The recorded Make workflow exited 2; the application resource report has
`mode: probe`, `passed: false` and the same error. Both workflow recordings
confirm their sources remained unchanged.

## Expected and observed manifest metadata

The [Iceberg specification, Manifest File Format](https://iceberg.apache.org/spec/#manifest-file-format)
requires the Avro key-value metadata property `content` for v2 manifests, with
string value `data` or `deletes`. This is distinct from the integer `content`
fields inside a manifest entry or manifest list.

Read-only inspection of the existing objects found:

| Writer / table | Manifest bytes | Header keys | `format-version` | `content` |
|---|---:|---:|---|---|
| DuckDB / `lab.probe` | 3,288 | 8 | `2` | `data` |
| ClickHouse / `lab.reverse` | 9,704 | 7 | `2` | absent |

Both contain `avro.codec`, `avro.schema`, `schema`, `schema-id`, `partition-spec`,
`partition-spec-id` and `format-version`. Only the DuckDB-written manifest contains
`content`. The inspected ClickHouse manifest is the same object named in DuckDB's
error. Inspection exited 0 and recorded `inspection_passed: true`; that means the
inspection succeeded, not that the ClickHouse manifest conforms to the spec.

Relevant writer code:
[IcebergWrites.cpp, pinned ClickHouse release](https://github.com/ClickHouse/ClickHouse/blob/v26.10.10.3-stable/src/Storages/ObjectStorage/DataLakes/Iceberg/IcebergWrites.cpp#L568-L575).
The requested correction is to emit the required manifest header with the
appropriate value and cover reading the committed output through an independent
Iceberg implementation.

## Integrity boundary and retained evidence

Separate ClickHouse exports verified every expected row body in both tables:
10,000 in `lab.probe`, 20,000 in `lab.reverse`. Their saved CSV SHA-256 values are
`b6bd095ce8ef39eabfc3b4f658752254e332a6e5bf183fa1ff675e7be45db844` and
`1d8ad1223d1778c04b27e68cab97cd6f3d37e2ac96a6bb3ee537e73f7d538186`.

This demonstrates an interoperability/spec-metadata failure. These observations
do not establish corruption, acknowledged data loss, a performance regression or
production readiness. No manifest was repaired during inspection.

Raw local evidence is retained under ignored `results/current/qualification-20261122/`:

- `probe/console.log`, `workflow.json`, `profile.json` and
  `command-resources-968850336.json`: failed native probe, effective service
  versions/image IDs and resource/error recording.
- `manifest-inspection/headers.json`, `workflow.json`, `avro_headers.py` and
  `run.py`: independent header inspection and source/exit evidence.
- `manifest-inspection/probe-body.json`, `reverse-body.json` and their CSV files:
  complete-body controls.

The raw resources and short probe duration are diagnostic evidence, not benchmark
results. This draft contains no claim that a different ClickHouse release fixes
this failure.

Additional reader check (2026-11-23): the [native Spark compatibility gate](../../duckdb-clickhouse/docs/CONNECTOR_COMPACTOR_PROBE.md) reads all 3,000 complete records from another freshly created v2 table written by the same ClickHouse release. All three of that snapshot's manifests also lack `content`, as directly inspected through Avro. Spark acceptance does not establish v2 writer conformance; the specification requirement and recorded DuckDB failure remain. This result permits testing the native Spark maintenance path, not declaring all readers compatible.
