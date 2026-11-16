# GreptimeDB: failed WAL initialization and offset-zero replay

A minimal upstream reproducer for the failure found in the operator example.
It intentionally starts metadata before a required remote-WAL topic exists with
`auto_create_topics=false`. This is an invalid bootstrap ordering: the question
is whether the server fails closed, or accepts writes after its WAL initialization
listener fails. It is not a production topology or a capacity benchmark.

## Quick start

Requires Docker Compose, Make and Python 3; allow 5 GiB Docker RAM. Images are
pinned: GreptimeDB 1.3.1, Redpanda 26.2.3 and etcd 3.6.16. No ports are published.
The Go application uses only the standard library and is built by its Dockerfile.

1. `make docker-up` — start owned WAL/metadata dependencies and capture the intentional initialization failure.
2. `make up` — create the previously unavailable empty topic; start data/query services and acknowledge `ROWS=100` deterministic records.
3. `make test` — verify every acknowledged ID, SHA-256 body and timestamp before restart.
4. `make down` — stop the database services while preserving evidence and volumes.
5. `make docker-down` — remove only this project's containers, volumes, network and workload image.

Run the following between steps 3 and 4:

- `make reproduce`: SIGKILL the datanode, prove exit 137, restart with the same
  volume, wait for native WAL replay and read every acknowledged record again.
  **Data loss remains a nonzero test failure**, with `restart-result.json` and the
  original ACK journal retained. A failed command is not a passing durability test.
- `make bootstrap-check`: require a native no-op at Kafka offset zero. It rejects
  an application entry at that offset, including after manual repair.
- `make repair`: explicitly replay absent deterministic records from the external
  fixture, then verify complete data. It never drops tables or resets storage.
  The original loss report and pre-repair readback remain separate evidence.

## Healthy control

After the full cleanup, move the previous ignored `results/` directory aside.
Run the same lifecycle with `CONTROL=1` on each command. This creates the topic
before metadata starts and requires successful native topic-pool activation.
`make reproduce CONTROL=1` then checks retained data after the same process crash.
The workload, configuration and fault are otherwise identical. Use independent
clean volumes; never change bootstrap ordering on a populated failed fixture to
conceal its loss.

## Results

Executed 2026-11-16, native ARM64 Docker, shared 11-CPU / 24.42 GiB VM.
The unhealthy metadata service logged a `WalProvider` initialization failure but
continued serving. The application then wrote 100 records in one acknowledged
batch. Every record was verified before SIGKILL; the same retained-volume restart
replayed zero rows and lost all 100 acknowledged records.

| Case | ACKed before restart | Verified before | Verified after | Lost ACK rows | Guard |
|---|---:|---:|---:|---:|---|
| Required topic missing during metadata initialization, created afterward | 100 | 100 | 0 | 100 | Rejected application entry at offset zero |
| Same failed fixture after explicit application repair | Original 100 | 0 before repair | 100 after repair | Original loss remains recorded | Still rejected unsafe bootstrap |
| Topic exists before metadata starts; native no-op preparation succeeds | 100 | 100 | 100 | 0 | Accepted no-op at offset zero |

Both crash cases proved exit 137 and waited for native replay. No tables or
volumes were reset to obtain recovery: the failed fixture was repaired explicitly
from its external deterministic witness, verified, then removed through the full
cleanup before the independent control was created. The failed run, original
readback and repair journal are preserved under
`results/archive-unsafe-1794793432221502000/`. The healthy control's full lifecycle and
cleanup passed too. This is a 100-row correctness reproducer, not a benchmark.
`results/` is ignored. The owning Compose project is `greptime-wal-bootstrap`.

## Source trail and limits

The original operator run failed `WalProvider` initialization with
`LeaderNotAvailable`. This reproducer substitutes a deterministic unavailable
partition/topic at the same preparation stage; it does not claim the two bootstrap
triggers are identical. There is one copy of each component to isolate the path;
no HA, independent-host durability, S3 recovery or live-throughput claim is made.

- [Pinned topic activation](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/common/meta/src/wal_provider/topic_pool.rs).
- [Pinned no-op preparation](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/common/meta/src/wal_provider/topic_creator.rs).
- [Pinned WAL replay starts after the flushed entry](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/mito2/src/region/opener.rs).
- [Pinned Kafka offset-to-entry mapping](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/log-store/src/kafka/log_store.rs).
