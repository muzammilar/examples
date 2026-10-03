# --cluster_mode=emulated: a single node that answers the CLUSTER commands as a one-shard cluster
# owning all 16384 slots, so cluster-mode client libraries can connect to it.
c() { valkey-cli -h dragonfly "$@"; }

c CLUSTER INFO | grep -E 'cluster_state|cluster_slots_assigned'
c CLUSTER SHARDS | tee /tmp/shards | paste -s -d ' ' -
grep -A2 -x slots /tmp/shards | tail -2 | paste -s -d ' ' - | grep -qx '0 16383' ||
	{ echo "FAIL: expected one shard with slots 0-16383"; exit 1; }

# a cluster client (-c) follows the slot map; every slot lands on this node, so no redirects
echo "CLUSTER KEYSLOT user:1 -> $(c CLUSTER KEYSLOT user:1)"
c -c SET cluster:test ok > /dev/null
v=$(c -c GET cluster:test)
echo "valkey-cli -c SET + GET cluster:test -> $v"
[ "$v" = ok ] || { echo "FAIL: SET/GET through a cluster client"; exit 1; }
