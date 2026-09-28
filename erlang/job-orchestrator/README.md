# job-orchestrator

**A proof of concept for learning Erlang/OTP, not a production service.**

A TCP job service in plain OTP, running as a cluster of three to five nodes
behind a load balancer. Clients send jobs over a socket; each node queues
them and hands them to a pool of workers. A job can carry a key, and every
job with the same key runs on the same node, chosen by consistent hashing.

## Testing

```bash
# Start a three-node cluster behind a load balancer (NODES=4 or 5 for more).
# The first run generates the nodes' shared Erlang cookie into .env (git-ignored).
make up

# Send requests, one per line
make send REQ='fib 30\nfib 30 key=user42\nowner user42'

# Submit a job, disconnect, and collect its result later on a new connection
make send REQ='submit sleep 3000 key=user42'
make send REQ='result <id from the reply>'

# List the cluster's members
make nodes

# Load-test through the load balancer: throughput and latency percentiles
make bench

# Walk through key ownership, load, and losing a node (see demo.sh)
make demo

# Stop all services and remove their volumes; images and build caches stay
make down
```

## How it works

```text
                        clients
                           │ TCP :5555
                     ┌─────▼─────┐
                     │  HAProxy  │  round-robin
                     └─┬───┬───┬─┘
             ┌─────────┘   │   └─────────┐
        ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
        │  node1  │◄─►│  node2  │◄─►│  node3  │  Erlang distribution
        └─────────┘   └─────────┘   └─────────┘
    each node:
                      orchestrator_sup (one_for_one)
      ┌──────┬──────────────┴──────────────┬───────────────────┐
     pg   cluster        pool_sup (rest_for_one)        tcp_sup (rest_for_one)
                          ┌──────┴───────┐              ┌───────┴────────┐
                     job_server     worker_sup      conn_sup         listener
                                     ├─ worker ─ task  ├─ conn ◄── client
                                     └─ ...            └─ ...
```

Nodes connect over Erlang distribution, retrying every second, and start in
any order. Every job server joins the `pg` group `job_servers`; whenever that
group changes, each node builds the same
[Maglev](https://research.google/pubs/maglev-a-fast-and-reliable-software-network-load-balancer/)
table over the members, which gives each node a near-equal share of keys. When
a node leaves, only its keys move; when it comes back, it takes them back.

A job with `key=K` goes to the job server on K's owner; one without a key runs
on the node the client reached. `submit <job>` detaches a job: the reply is a
job ID naming the node it went to, and `result <id>` fetches the outcome later
through any node, for `result_ttl` (five minutes by default). HAProxy spreads
connections round-robin and skips nodes that fail its health check.

## Protocol

One request per line. Replies come back in request order. Any job may end in
`key=K`.

| Request | Reply |
|---------|-------|
| `fib 30` | `ok 832040` |
| `sleep 500 key=user42` | `ok`, after running on user42's owner |
| `crash` | `error crashed` (after retries) |
| `submit sleep 500 key=user42` | `ok job 3f9c2a1b8d4e6f70.orchestrator@node3` |
| `result 3f9c2a1b8d4e6f70.orchestrator@node3` | `pending`, then the job's reply, e.g. `ok` |
| `result` of an unknown or expired ID | `error not_found` |
| `owner user42` | `ok orchestrator@node3` |
| `nodes` | `ok orchestrator@node1 orchestrator@node2 orchestrator@node3` |
| `status` | `ok queued=0 idle=4 busy=0`, for the node that answered |

A job can also fail with `error timeout` (past `job_timeout`), `error busy`
(queue full), `error unavailable` (job server restarting) or
`error bad_request`.

## Poking at it

`make demo` runs [`demo.sh`](demo.sh) against a three-node cluster. On a
laptop:

```text
==> Key owners, asked through different nodes
user1  ok orchestrator@node3
user2  ok orchestrator@node1
user3  ok orchestrator@node3
...
==> Detached job: submit, hang up, collect later
job 8c1d0e5f2a7b3946.orchestrator@node3
pending
ok
==> Unkeyed 10ms jobs, spread over every node
2000 ok   1032 jobs/s   p50 32.9ms   p95 34.9ms   p99 37.1ms   max 58.0ms
==> Keyed 10ms jobs, all on one owner
1000 ok   351 jobs/s   p50 90.3ms   p95 94.4ms   p99 99.8ms   max 100.4ms
==> Stopping node3, owner of user1
ok orchestrator@node1 orchestrator@node2
user1  ok orchestrator@node2
user2  ok orchestrator@node1
user3  ok orchestrator@node2
...
==> Restarting node3
user1  ok orchestrator@node3
```

Unkeyed jobs use all three nodes' workers; jobs for one key queue on its owner's four.

`make bench ARGS="..."` takes `--clients`, `--requests` and `--task`, e.g.
`--task 'sleep 10 key=user1'`. Settings come from the environment (`WORKERS`,
`MAX_QUEUE`, `JOB_TIMEOUT_MS`), e.g. `WORKERS=16 make up`. The load balancer
also listens on `localhost:5555`.

## Known limitations

`git grep Limitation` finds each of these in the code.

- Jobs live in memory. If a node's job server restarts or the node goes down,
  its queued and running jobs are lost, and so are kept detached results, of
  which there's no cap.
- The cluster has no notion of a network split: both sides can own the same key.
- Anyone with the Erlang cookie who can reach a node's distribution port can run
  code on it, and traffic between nodes isn't encrypted.
- No client authentication and no cap on connections.
- A client that half-closes mid-job still has that job run, up to `job_timeout`.

## Development

```sh
make test      # eunit; Common Test, including a suite that starts a real 3-node cluster
make lint      # erlfmt, xref, dialyzer
make quality   # elvis, hank, hadolint, shellcheck
make check     # all of the above
make shell     # then: orchestrator_job_server:run({fib, 10}).
```
