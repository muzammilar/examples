"""Request/reply: a responder service answering on a subject, and a client calling it."""
import asyncio
import os

import nats
from nats.errors import NoRespondersError


async def main():
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://localhost:4222"))

    # The "service": a queue subscription (so it can scale out) that replies
    # to msg.reply, the unique inbox subject the requester is listening on.
    async def upper(msg):
        await msg.respond(msg.data.decode().upper().encode())

    sub = await nc.subscribe("svc.upper", queue="upper-svc", cb=upper)
    await nc.flush()

    # The client: request() publishes with a reply inbox and awaits one answer.
    for word in ("ping", "nats"):
        resp = await nc.request("svc.upper", word.encode(), timeout=1)
        print(f"[request]  svc.upper {word!r} -> {resp.data.decode()!r}")

    # With no subscriber on the subject, the server answers immediately with a
    # "no responders" status instead of making the client wait for a timeout.
    await sub.unsubscribe()
    try:
        await nc.request("svc.upper", b"anyone?", timeout=1)
    except NoRespondersError:
        print("[request]  after unsubscribe -> NoRespondersError (no timeout wait)")

    await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
