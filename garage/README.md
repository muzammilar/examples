# Garage

Website: https://garagehq.deuxfleurs.fr/
GitHub mirror: https://github.com/deuxfleurs-org/garage

| Example | What it demonstrates |
|---|---|
| [s3-cluster](s3-cluster/) | Nine storage nodes, three-zone replication, independent live reads/writes, retained ACK histories, exact native owners/bytes, process/router crashes, permanent data-disk replacement and cold restart |

| Benchmark | Date | Setup | Result |
|---|---|---|---|
| [S3](s3-cluster/#benchmark) | 2026-11-16 | Nine nodes, three copies, data fsync, native ARM64; final peer/recovery configuration | 1 MiB / 16 clients: PUT 155.14 MiB/s, warm GET 458.55 MiB/s; 41,536 requests, zero failures |

Current stable requalification: [Garage 2.6.0](s3-cluster/#fresh-260-qualification--2026-11-27) passed the API/full-copy baseline and one live owner crash. Historical performance numbers above remain 2.5.0 results; interrupted multipart completion is next, not yet executed.

## Known issues

Bucket versioning is unsupported. SQLite NORMAL WAL is not a guarantee against
simultaneous all-node power loss. Default native RPC and block-check delays can
make full replica restoration much slower than renewed S3 availability. Native peer discovery is required for complete cold restarts; idle repair workers
do not prove all required native copies have returned. See the
example for the exact tested feature subset, numbers and recovery status.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [s3-cluster](s3-cluster/README.md#future-work--todo) | Exercise peer partitions and native quorum loss, measuring rejection, stale-read policy and convergence after healing. |
