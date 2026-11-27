# Flink local-mode orders

A bounded SQL job runs a local MiniCluster in one container/JVM. It emits one
million deterministic orders; a separate Python container verifies every body.
This is the minimal local execution shape, with no HA or process-recovery claim.

## Quick start

1. `make docker-up` builds the owned images and prepares the local output volume.
2. `make up` checks the example's output infrastructure without resetting data.
3. `make test` runs local SQL and independently verifies all one million rows.
4. `make down` removes only the verified order dataset.
5. `make docker-down` removes the owned volume/network and built images.

Docker Compose, Python 3 and Git are required. The application toolchain runs
inside containers. There is no long-running service to start for this bounded
local job. `make cli` starts an independent local SQL session.

## Source ownership

`sql/orders.sql` and `orders-verifier/verify.py` are meaningful symlinks to the
repository-owned Compose example's SQL/oracle source. `scripts/prepare.py`
materializes them as regular files under ignored `.build/`, using hard links
where supported and copies otherwise. Docker does not follow repository symlinks
automatically. Both source files are available in a clean checkout; this example
never executes a sibling Makefile or depends on sibling output/state. It owns its
images, named volume, internal network, results and lifecycle. Do not edit `.build/`.

## Contract and status

Expected IDs are 0–999,999, customer IDs modulo 10,000 and amounts of 100 plus
order ID modulo 1,000, totaling 599,500,000 cents. Missing, duplicate or incorrect
bodies fail the independent oracle. The test refuses an existing output directory before submission;
a failed run is preserved until explicitly removed through the lifecycle.

The 2026-11-26 local SQL and independent body verification passed: 1,000,000
orders, 599,500,000 cents, zero wrong/missing/duplicate rows. CSV output used
15,877,890 bytes / 15,884,288 allocated bytes; reference gzip used 4,869,875 bytes
(3.26×). The SQL actor's sampled CPU averaged 12.52% of one core and sampled
cgroup memory peaked at 505,688,064 bytes including page cache. Its intentionally
bounded datagen rate explains the idle time; these are not capacity results.
[Curated diagnostics and source inventory](reports/INITIAL_RESULTS.json).

The SQL actor has 1 CPU / 2 GiB and the verifier
1 CPU / 256 MiB. Resource measurements, CSV bytes and reference gzip compression
will be reported as functional diagnostics. Local single-JVM execution provides
neither replicated storage nor recovery from a failed process. Use the separate
Compose/operator projects for distributed task recovery; stateful windows,
ambiguous publication and independent restore remain further work.

[Local execution](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/dev/datastream/execution/local_execution/)
