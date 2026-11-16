"""Deliberate same-owner wrong-shard placement, expected detection, exact repair.

Run only in this isolated example. Preserve the acknowledged row and all reports.
This is a verifier negative control, not a simulated physical disk failure.
"""

import fcntl
import json
import os
from pathlib import Path

import control as lab


def main():
    os.chdir(Path(__file__).resolve().parent)
    with (lab.ROOT / ".controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        folder, _ = lab.fixture()
        lab.verify(folder)
        local = [row for row in lab.placements() if row["group"] == 1]
        source, target = local[0]["orders"], local[1]["orders"]
        node = lab.primary(1)
        lab.owned(node)
        row = lab.sql(
            node,
            f"SELECT op_id,customer_id,amount FROM public.orders_{source} ORDER BY op_id LIMIT 1",
        )
        if not row:
            raise RuntimeError("source shard has no acknowledged row to test")
        key, customer, amount = map(int, row.split("|"))
        predicate = f"op_id={key} AND customer_id={customer} AND amount={amount}"
        case = lab.check_directory(folder, "placement-negative")
        lab.exclusive_save(
            case / "before.json",
            {
                "at": lab.now(),
                "node": node,
                "source_shard": source,
                "target_shard": target,
                "op_id": key,
                "customer_id": customer,
                "amount": amount,
                "purpose": "wrong logical shard within correct owner group",
            },
        )
        try:
            try:
                # One local transaction changes only physical placement: preserve
                # the exact key/body and total row count, rather than add a duplicate.
                lab.sql(
                    node,
                    f"WITH moved AS (DELETE FROM public.orders_{source} WHERE {predicate} RETURNING customer_id,op_id,amount) INSERT INTO public.orders_{target} SELECT * FROM moved",
                )
                lab.wait_native(folder)
                try:
                    lab.tool(
                        folder,
                        case,
                        "verify",
                        node=node,
                        shard=1,
                        extra=lab.physical_queries(local),
                    )
                except RuntimeError:
                    report = json.loads((case / "check.json").read_text())
                    if report["wrong_shard"] != 1 or any(
                        report[name]
                        for name in (
                            "missing_acknowledged",
                            "wrong_values",
                            "duplicate_ids",
                            "partial_transactions",
                            "unexpected_rows",
                        )
                    ):
                        raise RuntimeError(
                            "negative control did not isolate wrong physical placement: "
                            + str(report)
                        )
                else:
                    raise RuntimeError(
                        "wrong logical shard passed physical verification"
                    )
            finally:
                # Idempotent repair also handles an uncertain response to injection.
                # Never delete an unexpected body or replay/reset an ACK history.
                repair = lab.primary(1)
                lab.owned(repair)
                lab.sql(
                    repair,
                    f"BEGIN; INSERT INTO public.orders_{source} SELECT * FROM public.orders_{target} WHERE {predicate} ON CONFLICT(customer_id,op_id) DO NOTHING; DELETE FROM public.orders_{target} WHERE {predicate}; COMMIT",
                )
                restored = lab.verify(folder)
        except BaseException as error:
            lab.exclusive_save(
                case / "failure.json", {"at": lab.now(), "error": str(error)}
            )
            raise
        lab.exclusive_save(
            case / "result.json",
            {
                "at": lab.now(),
                "expected_rejection": True,
                "oracle_report": report,
                "restored_verification": restored,
            },
        )
        print(
            "Wrong-shard placement rejected; exact ACK body restored:", case, flush=True
        )


if __name__ == "__main__":
    main()
