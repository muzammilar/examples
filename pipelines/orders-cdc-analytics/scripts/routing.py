"""Point-predicate routing with exact PostgreSQL bodies on every fixed replica."""

import json
import random

import lab


def main():
    path = lab.directory("routing")
    value = lab.fixture()
    seed = lab.ROOT / "results" / ("seed-initial-" + value["id"] + ".jsonl")
    records = [json.loads(line) for line in seed.read_text().splitlines()]
    n = max(
        row["last_id"]
        for row in records
        if row["event"] == "ack" and row["operation"] == "seed-orders"
    )
    ids = sorted(
        set(random.Random(42193).sample(range(1, n + 1), 64))
        | {1, 1000, 1001, 2000, 10000, n}
    )
    selected = ",".join(map(str, ids))
    expected = lab.command(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "lab",
        "-d",
        "orders",
        "-c",
        "COPY (SELECT o.id,o.customer_id,o.amount_cents,c.region,o.revision,c.revision,o.event_second,o.revision*1000000+c.revision FROM orders o JOIN customers c ON o.customer_id=c.id WHERE NOT o.cancelled AND o.id IN ("
        + selected
        + ") ORDER BY o.id) TO STDOUT",
    )
    (path / "expected.tsv").write_text(expected)
    columns = "order_id,customer_id,amount_cents,region,order_revision,customer_revision,event_second,version"
    for table in ("checkpointed", "asynchronous"):
        statements = ";".join(
            "SELECT "
            + columns
            + " FROM pipeline."
            + table
            + " WHERE order_id="
            + str(key)
            for key in ids
        )
        for replica in range(3):
            settings = (
                "SET enable_sql_cache=false;SET enable_query_cache=false;SET skip_missing_version=false;SET fallback_other_replica_when_fixed_corrupt=false;SET use_fix_replica="
                + str(replica)
                + ";"
            )
            actual = lab.command(
                "exec",
                "-T",
                "fe1",
                "mysql",
                "--batch",
                "--raw",
                "--skip-column-names",
                "-h127.0.0.1",
                "-P9030",
                "-uroot",
                "-e",
                settings + statements,
            )
            (path / (table + "-replica-" + str(replica) + ".tsv")).write_text(actual)
            if actual != expected:
                raise RuntimeError(
                    "Point routing body/absence mismatch: "
                    + table
                    + " replica "
                    + str(replica)
                )
    lab.save(
        path / "result.json",
        {
            "seed": 42193,
            "selected_ids": ids,
            "point_predicates_per_table_per_replica": len(ids),
            "sinks": 2,
            "fixed_replicas": 3,
            "matching_bodies_and_absences": True,
            "scope": "Point predicates, not proof of physical hash algorithm or host independence",
        },
    )
    lab.save(path / "status.json", {"passed": True})
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
