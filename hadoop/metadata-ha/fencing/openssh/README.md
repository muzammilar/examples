# Bounded OpenSSH fencing: not yet qualified

This variant uses Hadoop's native `shell(...)` fencing hook and the system
OpenSSH client, without patching Hadoop JARs. The parent lab owns all state and
lifecycle. `Dockerfile` creates the ZKFC-only variant; data-plane images remain
unchanged. The original failed [native JSch variant](../native-jsch/) is retained.

The script permits only the owned `nn1`/`nn2` RPC port 9000, uses generated lab
keys with strict pinned-host verification and bounds SSH execution to 12 seconds
(plus a two-second forced-kill grace). It invokes native `fuser`, then requires an
explicit local connection refusal. Transport errors or an uncertain listener
state fail closed. The host controller separately requires the paused PID in the
fencing proof and its disappearance before accepting the new active.

Source reviewed against [Hadoop 3.5 ShellCommandFencer](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-common-project/hadoop-common/src/main/java/org/apache/hadoop/ha/ShellCommandFencer.java),
which exports source/target fencing parameters and supplies no execution timeout.
Build and live failover passed after adding a bounded wait for asynchronous socket
closure. The first immediate-probe attempt failed its PID-proof gate and remains
preserved under `results/failover-1795786046061801000/`. The passing trial observed
promotion/fencing in 29.317 seconds; negative controls and fresh lifecycle remain
pending. This is a trusted local
lab, not a production fencing/security qualification.
