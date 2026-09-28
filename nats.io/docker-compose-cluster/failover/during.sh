# $1 = the stopped stream leader. The two remaining replicas still form a quorum (2 of 3).
old=$1
echo "waiting for a new stream leader (at most 30 s)"
for i in $(seq 30); do
	leader=$(nats stream info EVENTS --json 2>/dev/null | jq -r '.cluster.leader // empty')
	[ -n "$leader" ] && [ "$leader" != "$old" ] && break
	sleep 1
done
echo "new stream leader after ${i}s: $leader"
[ -n "$leader" ] && [ "$leader" != "$old" ] || { echo "FAIL: no new leader elected"; exit 1; }

# publishing waits for the JetStream ack, i.e. the write reached a quorum of the replicas
nats pub --jetstream events.during 'during {{Count}}' --count 5
nats consumer next EVENTS worker --count 5 --raw

nats stream info EVENTS --json > /tmp/info
jq -c '{messages: .state.messages, leader: .cluster.leader, replicas: [.cluster.replicas[] | {name, current, offline: (.offline // false)}]}' /tmp/info
[ "$(jq .state.messages /tmp/info)" = 10 ] || { echo "FAIL: expected 10 messages"; exit 1; }
[ "$(jq --arg o "$old" '[.cluster.replicas[] | select(.name == $o and (.offline or (.current | not)))] | length' /tmp/info)" = 1 ] ||
	{ echo "FAIL: expected $old listed as a lost replica"; exit 1; }
nats consumer info EVENTS worker --json > /tmp/c
jq -c '{consumer_leader: .cluster.leader, ack_floor: .ack_floor.stream_seq, num_pending}' /tmp/c
[ "$(jq .ack_floor.stream_seq /tmp/c)" = 10 ] && [ "$(jq .num_pending /tmp/c)" = 0 ] ||
	{ echo "FAIL: expected all 10 messages acked"; exit 1; }
