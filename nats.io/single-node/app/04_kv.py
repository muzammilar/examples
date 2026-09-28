"""Key/value: a bucket (a stream underneath) with put/get, a live watch, and history."""
import asyncio
import os

import nats
from nats.js.errors import BucketNotFoundError, NotFoundError

BUCKET = "py-prefs"


async def main():
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://localhost:4222"))
    js = nc.jetstream()

    # Fresh bucket each run; keep the last 5 revisions of every key.
    try:
        await js.delete_key_value(BUCKET)
    except (BucketNotFoundError, NotFoundError):
        pass
    kv = await js.create_key_value(bucket=BUCKET, history=5)

    # Watch keys matching a pattern; updates arrive as they are written.
    watcher = await kv.watch("theme.*")

    async def watch():
        async for entry in watcher:
            if entry is None:  # marker: initial (existing) values have all been delivered
                continue
            op = entry.operation or "PUT"
            val = f"={entry.value.decode()}" if entry.value else ""
            print(f"[watch]    {op} {entry.key}{val} rev={entry.revision}")

    task = asyncio.create_task(watch())

    # Every put returns the new revision (the stream sequence of that write).
    for color in ("blue", "red", "green"):
        rev = await kv.put("theme.color", color.encode())
        print(f"[put]      theme.color={color} rev={rev}")
    await kv.put("theme.font", b"mono")

    entry = await kv.get("theme.color")
    print(f"[get]      theme.color={entry.value.decode()} rev={entry.revision}")

    history = await kv.history("theme.color")
    print(f"[history]  theme.color: {[(e.revision, e.value.decode()) for e in history]}")

    await kv.delete("theme.font")  # a delete is a tombstone revision, also seen by watchers
    await asyncio.sleep(0.5)
    task.cancel()
    await watcher.stop()
    print(f"[keys]     {await kv.keys()}")

    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
