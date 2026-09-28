# R3 stream EVENTS + an R3 durable consumer; 5 messages published and consumed.
nats stream rm -f EVENTS > /dev/null 2>&1 || true
nats stream add EVENTS --subjects 'events.>' --storage file --replicas 3 --defaults > /dev/null
nats consumer add EVENTS worker --pull --ack explicit --replicas 3 --defaults > /dev/null
nats pub --jetstream events.before 'before {{Count}}' --count 5
nats consumer next EVENTS worker --count 5 --raw
nats stream info EVENTS --json | jq -c '{messages: .state.messages, leader: .cluster.leader, followers: [.cluster.replicas[] | {name, current}]}'
