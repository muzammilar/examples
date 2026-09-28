# Core NATS: fire-and-forget pub/sub with a wildcard subscriber
nats sub 'greet.>' --count 2 &
sleep 1
nats pub greet.en hello
nats pub greet.fr bonjour
wait

# Request/reply: a responder on svc.echo answers one request
nats reply svc.echo 'echo: {{Request}}' --count 1 &
sleep 1
nats request svc.echo ping
wait
