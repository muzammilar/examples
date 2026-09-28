# $1 = the restarted server: it rejoins as a follower and catches up on the 5 messages it missed.
old=$1
echo "waiting until all 3 replicas are current (at most 60 s)"
for i in $(seq 60); do
	nats stream info EVENTS --json > /tmp/info 2>/dev/null &&
		[ "$(jq '[.cluster.replicas[] | select(.current)] | length' /tmp/info)" = 2 ] && break
	sleep 1
done
jq -c '{messages: .state.messages, leader: .cluster.leader, replicas: [.cluster.replicas[] | {name, current}]}' /tmp/info
[ "$(jq -r '[.cluster.leader, .cluster.replicas[].name] | sort | join(",")' /tmp/info)" = "nats-1,nats-2,nats-3" ] &&
	[ "$(jq '[.cluster.replicas[] | select(.current)] | length' /tmp/info)" = 2 ] ||
	{ echo "FAIL: $old did not catch up"; exit 1; }
echo "$old is current again after ${i}s"
# the restarted server's own copy of the stream, from its /jsz (via the system account)
nats --user sys --password sys server request --name "$old" jetstream --streams 1 |
	jq -c '{server: .server.name, stream: .data.account_details[].stream_detail[]? | select(.name == "EVENTS") | {name, messages: .state.messages, last_seq: .state.last_seq}}' | tee /tmp/own
[ "$(jq .stream.messages /tmp/own)" = 10 ] || { echo "FAIL: $old does not hold all 10 messages"; exit 1; }
