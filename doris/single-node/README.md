# Doris single-node order baseline

One FE and one BE from Doris 4.0.8, the upstream-maintained Stable branch.
One million deterministic orders use a Unique Key table with a sequence column.
An independent Go process streams every row and checks its complete expected body.

## Quick start

1. `make docker-up` builds and starts this example's FE and BE.
2. `make up` creates the database and schema.
3. `make test` seeds orders and reads every body through a new connection.
4. `make down` drops the example database.
5. `make docker-down` removes owned containers, volumes, network and images.

## Status and limits

The initial baseline and retained-backend crash case passed on 2026-11-26.
All 1,000,000 order bodies and 599,500,000 cents verified before and after the
observed exit-137 crash. The final native core-setting change also preserved
the complete dataset. [Report](reports/INITIAL_RESULTS.json)

| Observation | Measured value |
|---|---|
| Native engine version | doris-4.0.8-rc02-bc8ea1bac6d |
| Orders / wrong or missing bodies | 1,000,000 / 0 |
| Fetch and row-verification duration after crash | 413.675 ms |
| Native tablets / copies per tablet | 8 / 1 |
| Native table size display | 3.960 MB |
| Backend storage directory | 9,576,104 bytes |
| Effective native cores / memory limit | 2 / 2 GiB |

Fetch timing excludes query submission and initial server execution. It is one
functional observation. Native cgroup CPU/memory and per-device byte/operation
counters are in the report. Final backend counters begin after container
replacement. Short-lived workload accounting and complete phase I/O remain
incomplete. Native compression ratio is unavailable; table space includes
encoding and index overhead. This profile uses RF1.
The native server-side numbers function generates the baseline fixture.
External live producers and readers, mutable revisions, deletes,
independent restore and replicated placement checks are pending.

## Startup and recovery findings

The first schema creation failed because a listening BE had not yet reported
usable disk capacity. After a retained restart, the native backend table function
briefly returned `[E-240]FE address is not available`. Setup now checks actual
capacity and retries that specific startup error within a bounded readiness
window. Other SQL errors propagate. Earlier failed attempts remain preserved.

Run `python3 scripts/observe.py backend-restart` after a successful baseline.
It verifies existing rows, kills the BE, records native exit state, restarts
the retained volume and performs fresh full-body verification. This is a
quiesced committed-data recovery case; live availability remains pending.

Services have no published host ports and use an internal network with lab-only
unauthenticated root access. FE heap is reduced to 1 GiB for the lab. BE memory
is configured to 2 GiB within a 4 GiB container cap. Verify native effective
settings and readiness before interpreting results.

[Release selection](https://doris.apache.org/download/)
· [Current batch](../../docs/example-plans/flink-doris-spark-storage.md)

[Declared baseline contract and client settings](config/contract.json).
