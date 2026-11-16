"""Explicit repair for the preserved 10k-row failed bulk-routing smoke fixture.

This does not reset a dataset, replay ACKs or convert its failure report to a pass.
Only redundant wrong-shard copies are removed, after full canonical verification.
"""

import fcntl
import json
import os
from pathlib import Path
import time

import control as lab


def main():
    os.chdir(Path(__file__).resolve().parent)
    with (lab.ROOT / ".controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        folder, fixture = lab.fixture()
        runs = list((folder / "runs").glob("*/run.json"))
        if (
            not runs
            or len(runs) > 2
            or {p.parent.name for p in runs} - {"smoke-history-1", "smoke-history-2"}
        ):
            raise RuntimeError(
                "repair is limited to the original two failed smoke runs"
            )
        configurations = []
        for run in runs:
            config = json.loads(run.read_text())
            expected_base = 0 if run.parent.name == "smoke-history-1" else 10000
            if (
                config["base"],
                config["reserved"],
                config["batch"],
                config["customers"],
            ) != (expected_base, 10000, 100, 10000) or not (
                run.parent / "failure.json"
            ).exists():
                raise RuntimeError("not a preserved failed 10k routing fixture")
            configurations.append(config)
        active = lab.compose("ps", "-q", "--status", "running").split()
        for identity in active:
            info = json.loads(lab.run(["docker", "inspect", identity]))[0]
            if "lab.case" in info["Config"]["Labels"]:
                raise RuntimeError(
                    "a workload is still running; refusing placement repair"
                )
        acked = set()
        expected_customers = {}
        for run, config in zip(runs, configurations):
            for event in lab.read_events(run.parent / "writes.jsonl"):
                if event["stage"] == "ack":
                    acked.update(range(event["first"], event["first"] + event["count"]))
            for key in range(config["base"], config["base"] + 10000):
                expected_customers[key] = (key - config["base"]) // 100
        if acked != set(expected_customers):
            raise RuntimeError(
                "repair requires the complete original external ACK oracle"
            )
        route = json.loads((folder / "routing.json").read_text())
        before, canonical, extras = {}, set(), 0
        for shard in range(3):
            rows = [
                list(map(int, row.split("|")))
                for row in lab.sql(
                    lab.primary(shard),
                    "SELECT op_id,customer_id,amount FROM orders ORDER BY op_id",
                ).splitlines()
            ]
            before[str(shard)] = rows
            for key, customer, amount in rows:
                if (
                    key not in acked
                    or customer != expected_customers[key]
                    or amount != key % 10000 + 1
                ):
                    raise RuntimeError(
                        "unexpected/corrupt row; refusing duplicate-only repair"
                    )
                if route[customer] == shard:
                    canonical.add(key)
                else:
                    extras += 1
        if canonical != acked or extras == 0:
            raise RuntimeError("canonical copies are missing or no repair is needed")
        attempt = folder / "repairs" / str(time.time_ns())
        lab.exclusive_save(
            attempt / "before.json",
            {
                "canonical_ack_rows": len(canonical),
                "extra_wrong_shard_rows": extras,
                "rows": before,
            },
        )
        # All canonical bodies were checked before the first deletion. The native
        # hash predicate cannot select a row on its correct shard.
        for shard in range(3):
            lab.sql(
                lab.primary(shard),
                "BEGIN; CREATE TEMP TABLE hash_oracle(customer_id bigint) PARTITION BY HASH(customer_id); "
                + f"DELETE FROM orders WHERE op_id>=0 AND op_id<{max(acked) + 1} AND NOT satisfies_hash_partition('hash_oracle'::regclass,3,{shard},customer_id); COMMIT;",
            )
        proof = lab.verify(folder)
        lab.exclusive_save(
            attempt / "result.json",
            {
                "at": lab.now(),
                "removed_wrong_shard_duplicates": extras,
                "retained_ack_rows": len(acked),
                "verification": proof,
            },
        )
        print(
            "Explicit placement repair verified; original failed run retained:", attempt
        )


if __name__ == "__main__":
    main()
