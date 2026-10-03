"""Steady load for the failover tests: one client sends batches of BATCH transfers of 1 unit
from a source to a sink account, back to back, for DURATION seconds, and prints how many
transfers were acknowledged each second.

The client retries a request by itself while the cluster elects a new primary, so a failover
shows up as one slow request (the stall) and seconds with 0 transfers, not as errors. At the
end the sink's credits_posted must equal the number of acknowledged transfers: nothing the
cluster acked was lost, nothing was applied twice.

Accounts live on ledger 3 with ids from the start time, so every run starts from zero and
never touches client/demo.py's accounts (ledger 1).
"""
import os
import time

import tigerbeetle as tb

DURATION = float(os.environ.get("DURATION", "40"))
BATCH = int(os.environ.get("BATCH", "100"))
LEDGER = 3

base = time.time_ns() << 20  # ids unique per run
SRC, DST = base + 1, base + 2

with tb.ClientSync(cluster_id=0, replica_addresses=os.environ["TB_ADDRESS"]) as client:
    res = client.create_accounts([tb.Account(id=SRC, ledger=LEDGER, code=1),
                                  tb.Account(id=DST, ledger=LEDGER, code=1)])
    assert all(r.status == tb.CreateAccountStatus.CREATED for r in res), res

    next_id, acked, requests = base + 1000, 0, 0
    stall, stall_at, slow = 0.0, 0.0, 0
    start = time.monotonic()
    second, in_second = 0, 0
    print(f"load: {DURATION:.0f} s, batches of {BATCH} transfers", flush=True)
    while (now := time.monotonic()) - start < DURATION:
        batch = [tb.Transfer(id=next_id + i, debit_account_id=SRC, credit_account_id=DST,
                             amount=1, ledger=LEDGER, code=1) for i in range(BATCH)]
        next_id += BATCH
        t0 = time.monotonic()
        res = client.create_transfers(batch)
        took = time.monotonic() - t0
        bad = [r for r in res if r.status != tb.CreateTransferStatus.CREATED]
        assert not bad, bad[:3]
        acked += BATCH
        requests += 1
        if took > stall:
            stall, stall_at = took, t0 - start
        slow += took > 1.0
        # per-second progress; a stalled request is counted in the second it returned
        sec = int(time.monotonic() - start)
        if sec != second:
            print(f"  t={second:>3}s  {in_second:>6} transfers", flush=True)
            for gap in range(second + 1, sec):
                print(f"  t={gap:>3}s  {0:>6} transfers", flush=True)
            second, in_second = sec, 0
        in_second += BATCH
    elapsed = time.monotonic() - start

    dst = client.lookup_accounts([DST])[0]
    print(f"acked {acked} transfers in {requests} requests over {elapsed:.1f} s "
          f"({acked / elapsed:.0f}/s)")
    print(f"longest request: {stall * 1000:.0f} ms at t={stall_at:.1f}s; requests over 1 s: {slow}")
    print(f"sink credits_posted={dst.credits_posted} (expected {acked})")
    assert dst.credits_posted == acked, "acknowledged transfers missing or duplicated"
    print("OK")
