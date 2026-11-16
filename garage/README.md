# Garage

Website: https://garagehq.deuxfleurs.fr/
GitHub mirror: https://github.com/deuxfleurs-org/garage

| Example | What it demonstrates |
|---|---|
| [s3-cluster](s3-cluster/) | Nine storage nodes, three-zone replication, independent live reads/writes, retained ACK histories, exact native owners/bytes, process/router crashes, permanent data-disk replacement and cold restart |

| Benchmark | Date | Setup | Result |
|---|---|---|---|
| [S3](s3-cluster/#benchmark) | 2026-11-16 | Nine nodes, three copies, data fsync, native ARM64; final peer/recovery configuration | 1 MiB / 16 clients: PUT 155.14 MiB/s, warm GET 458.55 MiB/s; 41,536 requests, zero failures |

## Known issues

Bucket versioning is unsupported. SQLite NORMAL WAL is not a guarantee against
simultaneous all-node power loss. Default native RPC and block-check delays can
make full replica restoration much slower than renewed S3 availability. Native peer discovery is required for complete cold restarts; idle repair workers
do not prove all required native copies have returned. See the
example for the exact tested feature subset, numbers and recovery status.
