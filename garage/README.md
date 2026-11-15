# Garage

Website: https://garagehq.deuxfleurs.fr/
GitHub mirror: https://github.com/deuxfleurs-org/garage

| Example | What it demonstrates |
|---|---|
| [s3-cluster](s3-cluster/) | Nine storage nodes, three-zone replication, S3 byte integrity, process crash, permanent data-disk replacement and native repair |

| Benchmark | Date | Setup | Result |
|---|---|---|---|
| [S3](s3-cluster/#benchmark) | 2026-11-15 | Nine nodes, three copies, data fsync, native ARM64 | 1 MiB / 16 clients: PUT 166.08 MiB/s, warm GET 479.11 MiB/s |

## Known issues

Bucket versioning is unsupported. SQLite NORMAL WAL is not a guarantee against
simultaneous all-node power loss. Default native RPC and block-check delays can
make full replica restoration much slower than renewed S3 availability. See the
example for the exact tested feature subset, numbers and recovery status.
