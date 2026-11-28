"""Independent full-row snapshot oracle; never trust only Spark row counts."""

import csv
import hashlib
from pathlib import Path


def verify(path):
    path = Path(path)
    customers = {}
    with (path / "customers.csv").open(newline="") as stream:
        for row in csv.reader(stream):
            key = int(row[0])
            if key in customers:
                raise ValueError("duplicate customer")
            customers[key] = (row[1], int(row[2]))
    with (path / "orders.csv").open(newline="") as source, (path / "joined.csv").open(
        newline=""
    ) as output:
        actual = csv.reader(output)
        count = 0
        previous = 0
        for row in csv.reader(source):
            key, customer, amount, revision = map(int, row[:4])
            if key <= previous:
                raise ValueError("unordered/duplicate source key")
            previous = key
            if row[4] not in ("true", "false"):
                raise ValueError("invalid boolean")
            if row[4] == "true":
                continue
            region, customer_revision = customers[customer]
            expected = [
                str(key),
                str(customer),
                str(amount),
                region,
                str(revision),
                str(customer_revision),
                row[5],
                str(revision * 1000000 + customer_revision),
            ]
            if next(actual, None) != expected:
                raise ValueError(f"wrong or missing Spark body for {key}")
            count += 1
        if next(actual, None) is not None:
            raise ValueError("extra Spark body")
    return {
        "rows": count,
        "wrong": 0,
        "sha256": hashlib.sha256((path / "joined.csv").read_bytes()).hexdigest(),
        "csv_bytes": (path / "joined.csv").stat().st_size,
    }
