"""Validate durable attempt/outcome histories without contacting the store."""

import json


def records(path):
    if not path.exists():
        return []
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise RuntimeError("incomplete durable journal: " + str(path))
    return [json.loads(line) for line in data.splitlines()]


def attempted_objects(events):
    """Reject malformed/duplicate/out-of-order outcomes before reconciliation."""
    attempts, outcomes = {}, {}
    for event in events:
        if event["stage"] == "attempt":
            value = event["object"]
            key = value["key"]
            if key in attempts:
                raise RuntimeError("duplicate attempt: " + key)
            attempts[key] = value
        elif event["stage"] in ["ack", "ambiguous"]:
            key = event["key"]
            if key not in attempts or key in outcomes:
                raise RuntimeError("outcome without unique preceding attempt: " + key)
            outcomes[key] = event["stage"]
        else:
            raise RuntimeError("unexpected writer event")
    return [
        {"object": value, "acked": outcomes.get(key) == "ack"}
        for key, value in attempts.items()
    ]
