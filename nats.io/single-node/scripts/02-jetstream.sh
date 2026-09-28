# A stream persists every message published on orders.>
nats stream add ORDERS --subjects 'orders.>' --storage file --defaults > /dev/null
nats pub orders.new 'order {{Count}}' --count 3
nats stream info ORDERS --json | jq -c '{subjects: .config.subjects, storage: .config.storage, messages: .state.messages, last_seq: .state.last_seq}'

# A durable pull consumer with explicit acks; `next` acks each message it fetches
nats consumer add ORDERS worker --pull --ack explicit --defaults > /dev/null
nats consumer next ORDERS worker --count 3
nats consumer info ORDERS worker --json | jq -c '{ack_policy: .config.ack_policy, delivered: .delivered.stream_seq, ack_floor: .ack_floor.stream_seq, num_pending}'
