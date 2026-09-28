"""JetStream: a persisted stream, acked publishes, a durable pull consumer, redelivery."""
import asyncio
import os

import nats
from nats.js.api import AckPolicy, ConsumerConfig
from nats.js.errors import NotFoundError

STREAM, DURABLE = "EVENTS", "py-worker"


async def main():
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://localhost:4222"))
    js = nc.jetstream()

    # Start clean so the demo is re-runnable, then capture everything on events.>
    try:
        await js.delete_stream(STREAM)
    except NotFoundError:
        pass
    await js.add_stream(name=STREAM, subjects=["events.>"])

    # js.publish waits for the server's PubAck: the message is stored, with a seq.
    for i in range(3):
        ack = await js.publish("events.signup", f"user-{i}".encode())
        print(f"[publish]   user-{i} stored in {ack.stream} at seq={ack.seq}")

    # Durable pull consumer: the server tracks its position under the name;
    # un-acked messages are redelivered after ack_wait (seconds here).
    psub = await js.pull_subscribe(
        "events.>",
        durable=DURABLE,
        config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, ack_wait=2),
    )

    msgs = await psub.fetch(batch=3, timeout=2)
    for m in msgs:
        md = m.metadata
        if md.sequence.stream == 2:
            print(f"[fetch]     seq={md.sequence.stream} {m.data.decode()} -> NOT acked (simulated crash)")
            continue
        await m.ack()
        print(f"[fetch]     seq={md.sequence.stream} {m.data.decode()} -> acked")

    # After ack_wait expires the server hands the un-acked message out again.
    print("[wait]      sleeping 2.5s > ack_wait=2s ...")
    await asyncio.sleep(2.5)
    for m in await psub.fetch(batch=3, timeout=2):
        md = m.metadata
        print(f"[redeliver] seq={md.sequence.stream} {m.data.decode()} num_delivered={md.num_delivered}")
        await m.ack_sync()  # waits for the server to confirm the ack (ack() doesn't)

    info = await js.consumer_info(STREAM, DURABLE)
    print(f"[consumer]  ack_floor={info.ack_floor.stream_seq} num_pending={info.num_pending} "
          f"num_ack_pending={info.num_ack_pending}")

    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
