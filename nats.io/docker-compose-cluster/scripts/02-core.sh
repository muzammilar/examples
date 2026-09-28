# Core pub/sub across the cluster: subscribe on nats-1, publish on nats-3;
# the message crosses the route between the two servers.
nats --server nats://nats-1:4222 sub 'greet.>' --count 2 --raw > /tmp/got &
sleep 1
nats --server nats://nats-3:4222 pub greet.en hello
nats --server nats://nats-3:4222 pub greet.fr bonjour
wait
echo "received on nats-1: $(tr '\n' ' ' < /tmp/got)"
[ "$(cat /tmp/got)" = "$(printf 'hello\nbonjour')" ] || { echo "FAIL: expected hello, bonjour"; exit 1; }
