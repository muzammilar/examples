# Key/value bucket replicated on all three servers (a stream KV_config underneath)
nats kv rm -f config > /dev/null 2>&1 || true
nats kv add config --replicas 3 --history 5 > /dev/null
nats kv put config app.color blue
nats kv put config app.color red
v=$(nats kv get config app.color --raw)
echo "app.color = $v"
[ "$v" = red ] || { echo "FAIL: expected red"; exit 1; }
nats kv info config
# the bucket is the stream KV_config: check its replication there
nats stream info KV_config --json > /tmp/kv
jq -c '{stream: .config.name, replicas: .config.num_replicas, history: .config.max_msgs_per_subject,
	leader: .cluster.leader, followers: [.cluster.replicas[].name]}' /tmp/kv
[ "$(jq .config.num_replicas /tmp/kv)" = 3 ] && [ "$(jq '.cluster.replicas | length' /tmp/kv)" = 2 ] ||
	{ echo "FAIL: expected an R3 bucket"; exit 1; }
