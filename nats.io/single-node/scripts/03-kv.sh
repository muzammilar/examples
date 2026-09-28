# Key/value bucket (a stream underneath) keeping the last 5 revisions per key
nats kv add config --history 5 > /dev/null
nats kv put config app.color blue
nats kv put config app.color red
nats kv get config app.color
nats kv history config app.color
nats kv ls config
