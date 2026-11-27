# Native Hadoop/JSch fencing: failed qualification

Hadoop 3.5.0, bundled JSch 0.1.55, three JournalNodes and three ZooKeeper voters.
The parent lab owns this variant's infrastructure, keys, namespace and witness.
`config/hdfs-site.xml` preserves the tested native fencing configuration. Select
`FENCER_IMAGE=examples/hadoop-metadata-ha:3.5.0` for the original ZKFC image;
never switch policy while a fencing action is running.

The live failover case paused active `nn1` PID 38. SSH authenticated and native
`fuser` killed that process, but `nn2` remained standby past the 60-second bound.
A native thread dump later showed the election thread joining a STDERR stream
pump blocked in `readLine`, after about 200 seconds. OpenSSH control commands,
including the same remote `fuser` command after the kill, returned promptly.
This bounds the observed stall to the Hadoop/JSch command-output path; the exact
cause has not been isolated. Killing the old process alone did not restore service.

Raw immutable evidence remains under the parent's ignored
`results/failover-1795759156388160000/`, `results/stalled-native-fencer.log` and
`results/native-fencer-thread-dump.log`. Baseline physical hashes passed before
injection. After Docker went offline and was restarted, both NameNodes returned as standby.
With both ZKFCs stopped, confirmed manual promotion restored service. Fresh native
clients and three physical replica hashes verified every ACK, including this failed
run: no acknowledged file loss or wrong bodies were observed. This manual recovery
does not make automatic failover pass.

Automatic controllers were stopped before manual recovery. Do not force promotion
until the old active is proved fenced and both controllers are stopped. An
interrupted CLI is an ambiguous administrative outcome: inspect native roles before
retrying. Do not format metadata or reload the fixture.

The replacement [bounded OpenSSH path](../openssh/) is unqualified until tested;
its existence does not change this failed result.
