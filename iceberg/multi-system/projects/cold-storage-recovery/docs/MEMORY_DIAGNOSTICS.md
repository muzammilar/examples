# Native memory-category sampling

The resource recorder now saves cgroup `memory.stat` alongside charged memory,
CPU, disk bytes and I/O. Reports summarize `anon`, `file` and `kernel` separately;
these are not Go heap or process RSS. The readings are not atomic and need not
sum exactly to `memory.current`. Historical samples without the new section
retain their original summary shape, so old evidence can still be replayed.

On 2026-11-22 a local aarch64 Docker Desktop check collected two samples per
service over **4.15 observed seconds** after a retained-volume recovery and
read-only data audit. Only this example's storage and query services were active;
no application load was generated. This qualifies the recorder, not capacity.

| Service | CPU, core-s | CPU utilization, one core = 100% | Charged memory range, bytes | Anonymous memory range, bytes | File-cache range, bytes |
|---|---:|---:|---:|---:|---:|
| SeaweedFS 4.53, 1 CPU / 1 GiB | 0.032454 | 0.78% | 654,684,160–654,761,984 | 215,801,856–215,801,856 | 323,506,176–323,620,864 |
| ClickHouse 26.10.2.103, 2 CPUs / 1.5 GiB | 0.739135 | 17.81% | 1,288,351,744–1,290,031,104 | 580,071,424–583,434,240 | 460,300,288–462,610,432 |

SeaweedFS allocated storage remained **1,008,553,984 bytes**. Its Docker-VM block
counters observed **114,688 read bytes / 1 read I/O**, with zero recorded writes.
ClickHouse allocation changed **216,731,648 → 219,078,656 bytes**, with zero
recorded device I/O during this short interval. Missing devices remain unavailable;
these counters do not measure physical SSD latency. Compression was not remeasured
in this quiescent recorder check; the preceding snapshot audit recorded **1.5744×**
encoded Parquet chunk compression, separately from allocated storage.

Provenance: ignored raw samples and summaries under
`results/compactor-20261122/memory-categories-native/6c968e99-0373-4bb3-af91-04ebf08f99bd/`.
The six resource-control tests and the native two-service probe pass.

This change followed an observed storage OOM. The earlier run did not capture
memory categories, so its charged-memory growth cannot establish a heap leak or
identify the responsible cache. SeaweedFS's pinned
[reader-buffer setup](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/s3api/s3api_server.go)
and [budget implementation](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/filer/reader_cache_budget.go)
are relevant configuration controls; their tuning and a complete isolated
compactor recovery remain separate work.
