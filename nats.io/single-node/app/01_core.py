"""Core NATS: at-most-once pub/sub, wildcard subjects, and a queue group."""
import asyncio
import os
from collections import Counter

import nats


async def main():
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://localhost:4222"))

    # Wildcards: `*` matches exactly one token, `>` matches one or more tokens.
    async def on_greet(msg):
        print(f"[greet.*]  {msg.subject} -> {msg.data.decode()}")

    async def on_all(msg):
        print(f"[greet.>]  {msg.subject} -> {msg.data.decode()}")

    await nc.subscribe("greet.*", cb=on_greet)
    await nc.subscribe("greet.>", cb=on_all)
    await nc.publish("greet.en", b"hello")
    await nc.publish("greet.fr", b"bonjour")
    await nc.publish("greet.en.us", b"howdy")  # only `greet.>` matches this one
    await nc.flush()  # make sure the server has seen everything we sent
    await asyncio.sleep(0.2)

    # Queue group: subscribers sharing a queue name split the messages;
    # each message goes to exactly ONE member of the group.
    counts = Counter()

    def worker(name):
        async def handle(msg):
            counts[name] += 1
        return handle

    for name in ("worker-a", "worker-b"):
        await nc.subscribe("jobs.resize", queue="resizers", cb=worker(name))
    await nc.flush()  # subscriptions registered before we publish

    for i in range(20):
        await nc.publish("jobs.resize", f"job {i}".encode())
    await nc.flush()
    await asyncio.sleep(0.5)

    print(f"[queue]    20 jobs split across the group: {dict(sorted(counts.items()))}")
    assert sum(counts.values()) == 20, "each job delivered exactly once"

    await nc.drain()  # process pending messages, then close


if __name__ == "__main__":
    asyncio.run(main())
