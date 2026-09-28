# A stream replicated on all three servers (R3): one leader, two followers.
nats stream rm -f ORDERS > /dev/null 2>&1 || true
nats stream add ORDERS --subjects 'orders.>' --storage file --replicas 3 --defaults > /dev/null
nats pub orders.new 'order {{Count}}' --count 3

# the replicas apply writes asynchronously; wait (bounded) until both followers are current
for i in $(seq 30); do
	nats stream info ORDERS --json > /tmp/info
	[ "$(jq '[.cluster.replicas[] | select(.current)] | length' /tmp/info)" = 2 ] && break
	sleep 1
done
jq -c '{replicas: .config.num_replicas, messages: .state.messages, leader: .cluster.leader,
	followers: [.cluster.replicas[] | {name, current}]}' /tmp/info
[ "$(jq .state.messages /tmp/info)" = 3 ] || { echo "FAIL: expected 3 messages"; exit 1; }
[ "$(jq -r '[.cluster.leader, .cluster.replicas[].name] | sort | join(",")' /tmp/info)" = "nats-1,nats-2,nats-3" ] ||
	{ echo "FAIL: expected a leader and 2 followers on nats-1..3"; exit 1; }
[ "$(jq '[.cluster.replicas[] | select(.current)] | length' /tmp/info)" = 2 ] || { echo "FAIL: followers not current"; exit 1; }

# durable pull consumer (also R3) with explicit acks; `next` acks each message it fetches
nats consumer add ORDERS worker --pull --ack explicit --replicas 3 --defaults > /dev/null
nats consumer next ORDERS worker --count 3
nats consumer info ORDERS worker --json > /tmp/c
jq -c '{ack_policy: .config.ack_policy, replicas: .config.num_replicas, ack_floor: .ack_floor.stream_seq, num_pending, leader: .cluster.leader}' /tmp/c
[ "$(jq '.ack_floor.stream_seq' /tmp/c)" = 3 ] && [ "$(jq .num_pending /tmp/c)" = 0 ] ||
	{ echo "FAIL: expected all 3 messages acked"; exit 1; }
