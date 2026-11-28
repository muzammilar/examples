# DataNode outage: data access versus restored redundancy

Pinned Hadoop 3.5.0. The fault killed `dn1` during an active Spark publication job,
kept it unavailable while a fresh Arrow reader verified the older million-row
batch, then restarted the same retained container/storage. The producer was not
paused. Spark published the new complete batch; all fixed Doris replica reads
matched. HDFS's writer logged pipeline failures, but Spark recorded no failed task
attempts in this case. This is a process outage, not permanent disk loss.

The 180-second post-readback repair gate failed. A later native scan still showed
14 of 75 published blocks at RF2, no missing/corrupt blocks and `HEALTHY` status.
All three DataNodes were alive. Native JMX showed **14 pending reconstructions**
while `LowRedundancyBlocks` was zero. Those signals alone do not establish RF3.

The running NameNode's `/conf` endpoint returned
`dfs.namenode.reconstruction.pending.timeout-sec=300`. The pinned
[PendingReconstructionBlocks source](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-hdfs-project/hadoop-hdfs/src/main/java/org/apache/hadoop/hdfs/server/blockmanagement/PendingReconstructionBlocks.java)
uses a timeout monitor interval of `min(300 seconds, configured timeout)`;
expiry can therefore take 5–10 minutes at the default setting. The
[BlockManager](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-hdfs-project/hadoop-hdfs/src/main/java/org/apache/hadoop/hdfs/server/blockmanagement/BlockManager.java)
then requeues timed-out reconstruction work. Our native logs recorded all
14 timeout messages at **385.539–385.540 seconds after injection**. Subsequent
physical verification found **225 / 225 matching copies** across all three batches.

Full RF3 was first verified by **430.780 seconds**. This is an observation upper
bound, not an exact completion timestamp: the collector stopped with the failed
180-second case, and repair completed before the later observer began. Isolated
repair CPU/I/O is unavailable. No configuration, replication factor or data changed,
and no NameNode restart or reseeding occurred.

## Retained-state runbook

1. Keep the failed phase, manifests, load labels, `fault.json`, native metrics and
   resource logs. Do not remove volumes or run `docker-down` while diagnosing.
2. `make status` — identify live roles. Check missing/corrupt blocks, pending repair,
   actual per-block copies and disk headroom; `HEALTHY` alone is insufficient.
3. `make observe-repair` — watch native pending/low-redundancy counters and require
   every published block to regain RF3. The bounded observer does not mutate data,
   alter settings or erase the failed deadline verdict. Its nominal observation
   window extends to 660 seconds after the original injection.
4. `make verify` — independently read the preserved batches and all three Doris
   copies. The normal final `make down` also performs complete batch reconciliation.
5. Only after verified recovery, `make down`, then `make docker-down` for scoped
   cleanup. Both preserve ignored raw evidence on the host.

Changing the pending-reconstruction timeout is a separate, unrun configuration
experiment. A shorter timeout may reduce stale-request waiting but can reschedule
work prematurely under slow disks/network or a busy cluster. Do not retroactively
turn this failed 180-second observation into a passing tuned result. Permanent
DataNode/disk loss, NameNode loss, partitions, independent restore and sustained
pressure remain future work.
