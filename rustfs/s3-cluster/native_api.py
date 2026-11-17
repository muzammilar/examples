"""Pinned native admin calls using botocore's SigV4 signer."""

import concurrent.futures
import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

import workload as w
from native_health import heal_complete, member_identity


def request(endpoint, path, *, body=None, method=None):
    method = method or ("GET" if body is None else "POST")
    data = (
        body
        if isinstance(body, bytes)
        else (None if body is None else json.dumps(body).encode())
    )
    headers = {} if body is None else {"Content-Type": "application/json"}
    url = endpoint + path
    signed = AWSRequest(method=method, url=url, data=data, headers=headers)
    credentials = Credentials(
        os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]
    )
    S3SigV4Auth(credentials, "s3", "us-east-1").add_auth(signed)
    with urlopen(
        Request(url, data=data, headers=dict(signed.headers.items()), method=method),
        timeout=30,
    ) as response:
        return json.load(response)


def inventory():
    values = {}
    for i in range(4):
        endpoint = f"http://rustfs{i}:9000"
        view = request(endpoint, "/rustfs/admin/v3/info")
        view["ready"] = request(endpoint, "/health/ready")
        view["integrity"] = request(endpoint, "/rustfs/admin/v3/integrity/readiness")
        view["replacement"] = request(
            endpoint, "/rustfs/admin/v4/heal/replacement-recovery"
        )
        values[f"rustfs{i}"] = view
    return values


def heal(*, format_set=False):
    minimum_objects = 0
    if not format_set:
        _, required = w.Store.__new__(w.Store).expected(
            fault=(w.ROOT / "churn.jsonl").exists()
        )
        if not required:
            raise RuntimeError("no witnessed prefix for native object scan")
        minimum_objects = len(required)
    endpoint = "http://rustfs3:9000"
    path = (
        "/rustfs/admin/v3/heal/" if format_set else "/rustfs/admin/v3/heal/lab-objects"
    )
    options = {
        "recursive": True,
        "dryRun": False,
        "remove": False,
        "recreate": False,
        "scanMode": 2,
        "updateParity": False,
        "nolock": False,
        "readRepair": False,
    }
    if format_set:
        options.update({"pool": 0, "set": 0})
    started = request(endpoint, path, body=options)
    w.save("heal-start", started)
    print(json.dumps({"native_heal_started": started, "at": time.time()}), flush=True)
    token = started["clientToken"]
    query = "?" + urlencode({"clientToken": token})
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        value = request(endpoint, path + query, body=b"", method="POST")
        w.save("heal-status", value)
        print(
            json.dumps(
                {"summary": value.get("summary"), "outcome": value.get("outcome")}
            ),
            flush=True,
        )
        # Set repair may finish with an empty captured bucket scope. Its
        # membership/format checks precede a separate mandatory deep object scan.
        if heal_complete(value, minimum_objects=minimum_objects):
            return
        time.sleep(1)
    raise RuntimeError("native deep-heal deadline exceeded")


def read_required():
    store = w.Store()
    values, required = store.expected(fault=(w.ROOT / "churn.jsonl").exists())
    if not required:
        raise RuntimeError("no witnessed prefix to read")
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(store.get, [values[key] for key in sorted(required)]))
    value = {
        "objects": len(required),
        "bytes": sum(values[key]["size"] for key in required),
        "seconds": time.monotonic() - started,
        "scope": "complete required bodies; no listing of concurrently growing tail",
    }
    w.save("prefix-readback", value)
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "inventory":
        values = inventory()
        w.save("native-health", values)
        print(json.dumps(member_identity(values)))
    elif command in {"heal", "format-set"}:
        heal(format_set=command == "format-set")
    elif command == "read-required":
        read_required()
    else:
        raise ValueError("unknown native command")
