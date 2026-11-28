# Guest, disk and file-backup recovery

This bounded level-3 follow-up extends the existing CephFS lab. It does not add
another cluster owner or a throughput matrix. Guest loss, disk replacement and
fresh-cluster file-level restore passed; measured stalls and configuration gaps
remain in the parent README and curated reports.

## Held faults

After the normal baseline, run independent commands serially:

```sh
python3 scripts/observe.py guest-failure
python3 scripts/observe.py disk-failure
```

Use `PYTHON=python3.13` when the host default is too old. The executed follow-up
uses `FILES=128 PAYLOAD_BYTES=65536` for a bounded 8-MiB baseline. These fixture
parameters configure historical readers. Fault writers default to
`FAILURE_PAYLOAD_BYTES=65536`, independently of a larger seed, to bound stored
traffic; 600-second ceilings further limit their total work. Larger overrides
need additional headroom. The measured seed and live payloads were both 64 KiB. Timing during emulation is diagnostic, not production throughput.

The writer and historical reader run on guests 1 and 2. The fault targets guest
0, preserving those independent clients and their external-pool witness. Before
injection the host exports the witness outside the Docker volume. Each case must
observe native OSD membership loss, then enter a new phase and show at least five
new ACKs and three exact reads while the fault remains held. After recovery,
fresh clients verify every acknowledged body and all PGs must span three hosts.

`guest-failure` abruptly terminates the owned QEMU guest via QMP without guest
shutdown, holding its supervisor's restart marker. This is whole-guest power-off,
not a graceful node drain or physical host power-loss proof. Recovery removes the
marker, verifies a different kernel boot ID and awaits all nine OSDs and clean PGs.

`disk-failure` detaches only guest 0's allowlisted `cephlab-ceph0-d0` disk. It binds
its existing LVM/OSD UUID to the current FSID before recording a durable plan.
Recovery stops the old service, marks the OSD out, waits for repaired placement,
requires native `safe-to-destroy`, destroys that identity and creates a new UUID
on the empty allowlisted spare. The numeric OSD ID and CRUSH position are retained;
the old disk stays fenced, and the spare attachment is persisted for later boots.
A pre-existing replacement plan is not overwritten: this destructive case needs
a fresh fixture for another disk-loss trial. Failed recovery preserves the plan
and evidence for diagnosis, rather than zapping an uncertain device.

This follows the manually managed cluster's upstream
[OSD replacement procedure](https://docs.ceph.com/en/umbrella/rados/operations/add-or-rm-osds/).
No cephadm-managed cluster, host block device or foreign Docker volume is targeted.

## Independent file-level restore

Stop workload writers, verify all current ACKs, then:

```sh
python3 scripts/observe.py backup
# Record the printed results/backups/<id>/manifest.json path.
make down
KEEP_IMAGE=1 make docker-down
make -o build-guests docker-up
make -o build-workload up
BACKUP_MANIFEST=results/backups/<id>/manifest.json python3 scripts/observe.py restore
make down
make docker-down
```

The two `-o` options reuse the already built, pinned image and matching native
artifact. They skip compilation only; the free-space guard, guest provisioning
and fresh native cluster still run. The default five-command lifecycle always
builds prerequisites. `KEEP_IMAGE=1` preserves only that rebuildable image;
containers, the original state volume and guest disks are still destroyed.
Default final cleanup removes the image too.

Backup reads **actual CephFS bodies**, copies them with their external journals,
and validates every byte against the independent SHAKE/SHA256 oracle. Archive
validation rejects missing bodies, unexpected names, duplicates and links. A
manifest records checksums, exact ACK/byte totals and a quiescent cut. Restore
requires a different cluster FSID and no existing fixture; it writes archived
bytes into exclusive new paths and reruns fresh-client/native-placement checks.
It never substitutes deterministic data generation for restoration.

The guarantee is the immutable application ACK set at the saved cut. This is not
a backup of all CephFS metadata, ACLs/xattrs, hard links, configuration, CephX
identities or unacknowledged staging files. It is quiescent, not an atomic online
snapshot. Interrupted restore can leave partial destination files; exclusive
creation rejects overlay/retry, requiring diagnosis and a fresh destination.
Native metadata repair is a separate, untested operation; do not reset the MDS
journal to conceal loss. See upstream
[metadata recovery boundaries](https://docs.ceph.com/en/umbrella/cephfs/disaster-recovery-experts/).

## Evidence and metrics

Raw actor/ACK journals, native membership snapshots, before/after resource counters,
archive bodies and Docker samples stay in ignored `results/`. Only compact
reports and conclusions are tracked. Measure CPU utilization/core-seconds, charged
memory, disk read/write bytes and operations, storage allocation, repair time,
latencies and ACK gaps. Counters from a restarted guest have a new epoch and must
not be subtracted from its old boot. Native Ceph counters and QEMU's outer CPU
scope cannot be added. Compression is disabled; publish native pool compression
counters and logical/replicated storage separately, without inventing a codec
ratio or compression winner.

The installed pinned `ceph_volume/devices/lvm/create.py` was inspected: create
combines prepare and activate, and rolls back OSD-ID creation when activation
fails. The controller therefore records its intended UUID first and reconciles
native/LVM identity on re-entry; it must not assume a failed CLI left a complete
replacement. Upstream [v21.2.0 source](https://github.com/ceph/ceph/blob/v21.2.0/src/ceph-volume/ceph_volume/devices/lvm/create.py).

The external archive is outside the source Docker volume but remains on the same
computer. Fresh-volume restoration demonstrates logical independence, not a
separate physical backup failure domain or off-site disaster protection.
