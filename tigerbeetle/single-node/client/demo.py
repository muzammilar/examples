"""Accounts, a transfer, two-phase (pending -> post / void), a linked chain that fails
atomically, and a transfer rejected by debits_must_not_exceed_credits.

IDs are fixed, so a second run is idempotent: creates come back EXISTS (or
ID_ALREADY_FAILED for events that failed before) and the balances stay the same.
"""
import os

import tigerbeetle as tb

A, T = tb.AccountFlags, tb.TransferFlags
LEDGER = 1  # e.g. USD cents; transfers only move value within one ledger
OPERATOR, ALICE, BOB, MISSING = 1, 2, 3, 99


def step(title):
    print(f"\n==== {title}")


def show(kind, batch, results):
    for event, result in zip(batch, results):
        print(f"  {kind} id={event.id:<4} -> {result.status.name}")
    return [r.status for r in results]


def balances(client):
    print(f"  {'id':>3} {'debits_pending':>15} {'debits_posted':>14} {'credits_pending':>16} "
          f"{'credits_posted':>15}  flags")
    for a in sorted(client.lookup_accounts([OPERATOR, ALICE, BOB]), key=lambda a: a.id):
        print(f"  {a.id:>3} {a.debits_pending:>15} {a.debits_posted:>14} {a.credits_pending:>16} "
              f"{a.credits_posted:>15}  {A(a.flags).name}")


def transfer(id, debit, credit, amount, flags=T.NONE, pending_id=0):
    return tb.Transfer(id=id, debit_account_id=debit, credit_account_id=credit, amount=amount,
                       pending_id=pending_id, ledger=LEDGER, code=10, flags=flags)


ok = {tb.CreateAccountStatus.CREATED, tb.CreateAccountStatus.EXISTS,
      tb.CreateTransferStatus.CREATED, tb.CreateTransferStatus.EXISTS}

with tb.ClientSync(cluster_id=0, replica_addresses=os.environ["TB_ADDRESS"]) as client:
    step("create_accounts: operator (may go negative), alice + bob (debits_must_not_exceed_credits)")
    batch = [
        tb.Account(id=OPERATOR, ledger=LEDGER, code=1),
        tb.Account(id=ALICE, ledger=LEDGER, code=2, flags=A.DEBITS_MUST_NOT_EXCEED_CREDITS),
        tb.Account(id=BOB, ledger=LEDGER, code=2, flags=A.DEBITS_MUST_NOT_EXCEED_CREDITS),
    ]
    assert set(show("account ", batch, client.create_accounts(batch))) <= ok

    step("create_transfers: deposit 1000 to alice, 100 to bob (debit operator, credit user)")
    batch = [transfer(101, OPERATOR, ALICE, 1000), transfer(102, OPERATOR, BOB, 100)]
    assert set(show("transfer", batch, client.create_transfers(batch))) <= ok

    step("two-phase: alice -> bob 300 PENDING (reserved in debits_pending/credits_pending)")
    batch = [transfer(201, ALICE, BOB, 300, T.PENDING)]
    assert set(show("transfer", batch, client.create_transfers(batch))) <= ok
    balances(client)

    step("two-phase: POST_PENDING_TRANSFER 201; alice -> bob 50 PENDING, then VOID_PENDING_TRANSFER")
    batch = [
        transfer(202, ALICE, BOB, 300, T.POST_PENDING_TRANSFER, pending_id=201),
        transfer(203, ALICE, BOB, 50, T.PENDING),
        transfer(204, ALICE, BOB, 50, T.VOID_PENDING_TRANSFER, pending_id=203),
    ]
    assert set(show("transfer", batch, client.create_transfers(batch))) <= ok
    balances(client)

    step("linked chain: 301 alice -> bob 100 (LINKED) + 302 bob -> account 99 (does not exist)")
    batch = [transfer(301, ALICE, BOB, 100, T.LINKED), transfer(302, BOB, MISSING, 100)]
    statuses = show("transfer", batch, client.create_transfers(batch))
    assert tb.CreateTransferStatus.CREATED not in statuses, "the whole chain must fail"
    print("  -> 301 was valid on its own but is rolled back with 302: all or nothing")

    step("debits_must_not_exceed_credits: bob -> alice 10000 (bob only has 400)")
    batch = [transfer(401, BOB, ALICE, 10_000)]
    statuses = show("transfer", batch, client.create_transfers(batch))
    assert tb.CreateTransferStatus.CREATED not in statuses

    step("lookup_accounts: alice = 1000 - 300 = 700, bob = 100 + 300 = 400, operator = -1100")
    balances(client)
    got = {a.id: (a.credits_posted - a.debits_posted, a.debits_pending)
           for a in client.lookup_accounts([OPERATOR, ALICE, BOB])}
    assert got == {OPERATOR: (-1100, 0), ALICE: (700, 0), BOB: (400, 0)}, got
    print("\nOK")
