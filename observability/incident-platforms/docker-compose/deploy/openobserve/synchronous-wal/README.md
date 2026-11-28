# Synchronous WAL reference — bounded qualification executed

Parent lifecycle: `make bootstrap OPENOBSERVE_SERVICE=openobserve-sync`. This
component has its **own `openobserve-sync` volume** and an exclusive `openobserve`
DNS alias. The parent controller stops the buffered service first, checks the
active variant and never queries one variant as though it were the other's data.

`ZO_WAL_FSYNC_DISABLED=false` requests the native synchronous write path. The
image, logical projection, CPU/RAM and workload are otherwise unchanged. The bounded live SIGKILL/reconciliation case below passed the declared logical
projection and ACK-survival gate. It cannot repair the original buffered batch. A passing
process-restart case still does not establish host/disk power-loss durability,
independent backup or HA.

Qualification completed for this bounded process-crash case: healthy full
1,000,000 metrics / 1,000,000 logs / 30,000 spans passed. During the held ten-second
SIGKILL, all 941,000 metric ACKs, 966,000 log ACKs and 28,230 span ACKs survived;
old/new ACK visibility recovered in 14.625 seconds including the hold. No replay.
This is one process-crash run, not power-loss, disk-loss or HA qualification.
Native trace projection remains lossy. See [findings](../../../../docs/FINDINGS.md).
