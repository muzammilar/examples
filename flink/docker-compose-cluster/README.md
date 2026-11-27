# Flink distributed order processing

One JobManager and two one-slot TaskManagers process a deterministic million-order
SQL fixture. A separate checkpointed streaming case crashes one worker and
verifies restored output. Separate [local](../single-node/) and
[Kubernetes operator](../kubernetes-operator/) examples have initial executed results.

## Quick start

1. `make docker-up` builds and starts the isolated Flink services.
2. `make up` prepares output storage.
3. `make test` runs the batch and checks every output body.
4. `make down` removes the job output.
5. `make docker-down` removes the example services, volume and locally built image.

## Contract and limitations

The fixture contains 1,000,000 unique order IDs and 10,000 customers. A separate
CSV reader verifies every ID, customer and amount. The volume is shared by local
containers and has one physical storage failure domain. This batch job does not
establish streaming checkpoint guarantees or high availability.

The lab uses Flink SQL and a containerized Python standard-library verifier. The JVM runs the
upstream Flink engine. Compose owns all services and output. Host Python 3 is
used only by the resource observer. Repeated tests require `make down`, followed
by a separate `make up`, to avoid appending a second result set.

## Results

On 2026-11-26 the initial one-worker/two-slot batch and separate full-body verifier passed, followed
by workload and infrastructure teardown. Docker runs native ARM64 with 11 CPUs
and 24.4 GiB available. Every Flink role has 1 CPU and 1,600 MiB limits; the
verifier has 1 CPU and 256 MiB. The source intentionally uses the datagen rate
limit, so elapsed throughput is unsuitable for capacity ranking.

| Observation | Value |
|---|---|
| Verified rows / customers | 1,000,000 / 10,000 |
| Sum of amount_cents | 599,500,000 |
| Missing / duplicate / wrong rows | 0 / 0 / 0 |
| CSV bytes / allocated output bytes | 15,877,890 / 15,884,288 |
| TaskManager mean / peak memory | 550.65 MiB / 557.0 MiB |
| JobManager mean / peak memory | 544.57 MiB / 580.3 MiB |
| SQL-client mean / peak memory | 218.26 MiB / 226.6 MiB |
| TaskManager mean / peak CPU | 3.12% / 37.07% of one core |
| Output format | Uncompressed CSV |
| Reference gzip bytes / ratio | 4,869,875 bytes / 3.26× |

The gzip measurement compresses the completed output in the verifier and describes
a reference encoding. Flink stored uncompressed CSV. The initial resource
capture has rounded lifetime block-I/O counters and may miss short-lived verifier
usage. IOPS/device latency, build/startup cost and per-operation latency remain
unavailable. See the [compact report](reports/INITIAL_BATCH.json).

The first SQL attempt connected to `0.0.0.0:8081` and failed. Overriding the
upstream image entrypoint bypassed its property initialization. Explicit SQL-client
endpoint arguments fixed submission. Original logs remain in ignored
`results/failed-sql-client-logs/`. The checkpoint recovery case below is separate
from the initial batch; manager HA and capacity-pressure coverage remain pending.

## Future work

- [ ] Complete phase-aligned resources including the verifier, IOPS and operation timing.
- [x] Verify TaskManager placement in the separate operator example.
- [ ] Extend the checkpointed fixture with richer keyed state, an external acknowledgement witness and live readers.
- [ ] Test retained-volume recovery, savepoint rescaling and operator reconciliation.
- [x] Add separate single-process and private kind/operator deployments.

## Worker checkpoint recovery

On a fresh empty output directory, run `python3 scripts/observe.py task-recovery`.
The controller waits for a completed native checkpoint, SIGKILLs one owned
TaskManager, verifies exit 137, restarts it and requires native job state FINISHED
and an observed checkpoint restore. A separate reader verifies every output body.
Use `make down` and a separate `make up` before this case if output exists.

The corrected trial recovered 1,000,000 rows and 599,500,000 cents with zero
missing/duplicate/wrong rows. Native counters recorded two restores, 51 completed
and three failed checkpoints. The failed first trial retained only 27,848 final
rows after exhausting three one-second retries; the SQL client still exited zero.
The independent oracle rejected it. Up to ten retries with a ten-second delay allowed the replacement
worker to register and complete recovery in the subsequent trial.

[Recovery report](reports/TASK_RECOVERY.json). This bounded stream has a final
job acknowledgement. Per-record external witnesses, independently live readers,
stateful joins/windows and sustained outage budgets remain pending.

## Sources

[Stable releases](https://flink.apache.org/downloads/),
[upstream image](https://github.com/apache/flink-docker),
[state and checkpoints](https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/stateful-stream-processing/).

[Declared baseline contract and client settings](config/contract.json).
