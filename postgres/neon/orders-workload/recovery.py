"""Continuous native PostgreSQL transactions and an independent fsynced witness."""

import hashlib
import json
import os
from pathlib import Path
import urllib.request

import psycopg

ROOT = Path("/results")


def digest(key):
    return hashlib.sha256(f"recovery:{key}".encode()).digest()


def native_wal():
    ids = json.loads((ROOT / "ids.json").read_text())
    result = {}
    for i in range(3):
        url = f"http://sk{i}:7676/v1/tenant/{ids['tenant']}/timeline/{ids['timeline']}"
        with urllib.request.urlopen(url, timeout=10) as response:
            result[f"sk{i}"] = json.load(response)
    return result


def recover_wal():
    ids = json.loads((ROOT / "ids.json").read_text())
    for name in ("timeline", "branch"):
        if name not in ids:
            continue
        body = {
            "tenant_id": ids["tenant"],
            "timeline_id": ids[name],
            "http_hosts": ["http://sk1:7676", "http://sk2:7676"],
            "mconf": None,
        }
        request = urllib.request.Request(
            "http://sk0:7676/v1/pull_timeline",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            print(response.read().decode())


def verify_recovery():
    attempted, acknowledged, seen = set(), set(), set()
    batches = []
    files = sorted(ROOT.glob("fault-*.jsonl"))
    for path in files:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            target = (
                attempted
                if row["stage"] == "attempt"
                else acknowledged
                if row["stage"] == "ack"
                else None
            )
            if target is not None:
                target.update(range(row["first"], row["first"] + row["count"]))
            if row["stage"] == "attempt":
                batches.append((row["first"], row["count"]))
    if not acknowledged:
        raise RuntimeError("no historical acknowledged recovery rows")
    with psycopg.connect(os.environ["NEON_DSN"]) as db:
        with db.cursor(name="recovery_readback") as cursor:
            cursor.itersize = 10000
            cursor.execute(
                "SELECT event_id,customer_id,amount,payload FROM recovery_events"
            )
            for key, customer, amount, payload in cursor:
                if (
                    key not in attempted
                    or key in seen
                    or customer != key % 10000
                    or amount != key * 3
                    or bytes(payload) != digest(key)
                ):
                    raise RuntimeError(
                        f"unexpected, duplicate or corrupt recovery row {key}"
                    )
                seen.add(key)
    missing = acknowledged - seen
    partial = sum(
        sum(key in seen for key in range(first, first + count)) not in (0, count)
        for first, count in batches
    )
    result = {
        "cases": len(files),
        "acknowledged_rows": len(acknowledged),
        "observed_attempted_rows": len(seen),
        "missing_acknowledged": len(missing),
        "wrong_or_duplicate": 0,
        "partial_transactions": partial,
    }
    (ROOT / "recovery-history.json").write_text(json.dumps(result, indent=2) + "\n")
    if missing:
        raise RuntimeError(
            f"lost {len(missing)} acknowledged rows; first: {min(missing)}"
        )
    if partial:
        raise RuntimeError(f"{partial} COPY transactions were only partially committed")
    print(json.dumps(result))
