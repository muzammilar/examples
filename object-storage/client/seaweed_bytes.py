"""Verify every native volume replica against external full-object SHA-256.

Pinned SeaweedFS 4.53 filer metadata supplies chunk IDs and offsets. Native
?proxied=true forbids a volume server from forwarding to a different replica.
No placement, needle format, cookie, checksum, or manifest codec is implemented.
"""

import base64
import concurrent.futures
import hashlib
import json
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen
import workload as w


def get(url):
    with urlopen(url, timeout=30) as response:
        return response.read()


locations = {}
for node in range(9):
    status = json.loads(get(f"http://volume{node}:8080/status?pretty=y"))
    for v in status.get("Volumes") or []:
        locations.setdefault(str(v["Id"]), []).append(node)
store = w.Store.__new__(w.Store)
expected, required = store.expected(fault=(w.ROOT / "churn.jsonl").exists())
# Root runs following all cases contain historical ACKs but no current journal.
start = time.monotonic()


def one(key):
    value = expected[key]
    metadata = json.loads(
        get(
            "http://filer0:8888/buckets/"
            + w.BUCKET
            + "/"
            + quote(key, safe="/")
            + "?metadata=true"
        )
    )
    if int(metadata["FileSize"]) != value["size"]:
        raise RuntimeError("native filer size mismatch: " + key)
    chunks = sorted(metadata.get("chunks") or [], key=lambda c: int(c.get("offset", 0)))
    if not chunks:
        data = base64.b64decode(metadata.get("Content") or "", validate=True)
        if (
            len(data) != value["size"]
            or hashlib.sha256(data).hexdigest() != value["sha256"]
        ):
            raise RuntimeError("inline metadata body mismatch: " + key)
        return {"bytes": len(data), "inline": 1, "chunks": 0}
    hashes = [hashlib.sha256() for _ in range(3)]
    cursor = 0
    for chunk in chunks:
        if chunk.get("is_chunk_manifest") or chunk.get("cipher_key"):
            raise RuntimeError(
                "this unencrypted bounded corpus must contain direct chunks: " + key
            )
        if int(chunk.get("offset", 0)) != cursor:
            raise RuntimeError("unexpected chunk overlap/hole: " + key)
        fid = chunk["file_id"]
        volume = fid.split(",")[0]
        nodes = locations[volume]
        if len(nodes) != 3 or {n // 3 for n in nodes} != {0, 1, 2}:
            raise RuntimeError("expected three native zone replicas: " + volume)
        for node in nodes:
            data = get(f"http://volume{node}:8080/{quote(fid, safe=',')}?proxied=true")
            if len(data) != int(chunk["size"]):
                raise RuntimeError("replica chunk length mismatch: " + key)
            hashes[node // 3].update(data)
        cursor += int(chunk["size"])
    if cursor != value["size"] or any(h.hexdigest() != value["sha256"] for h in hashes):
        raise RuntimeError("one or more physical zone copies differ: " + key)
    return {"bytes": cursor, "inline": 0, "chunks": len(chunks)}


# Negative control: a server outside a known volume's replica set must refuse
# the read, rather than transparently making a valid remote copy look local.
probe = json.loads(
    get("http://filer0:8888/buckets/" + w.BUCKET + "/data/large/00000000?metadata=true")
)["chunks"][0]["file_id"]
wrong = next(n for n in range(9) if n not in locations[probe.split(",")[0]])
try:
    get(f"http://volume{wrong}:8080/{probe}?proxied=true")
except HTTPError as error:
    if error.code != 404:
        raise
else:
    raise RuntimeError("non-owner served a proxied copy; replica audit is invalid")
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
    values = list(pool.map(one, sorted(required)))
report = {
    "objects": len(values),
    "logical_bytes": sum(v["bytes"] for v in values),
    "physical_zone_body_bytes_verified": 3
    * sum(v["bytes"] for v in values if not v["inline"]),
    "inline_objects_verified_from_metadata": sum(v["inline"] for v in values),
    "direct_replica_chunk_reads": 3 * sum(v["chunks"] for v in values),
    "mismatched_objects": 0,
    "non_owner_negative_control": True,
    "seconds": time.monotonic() - start,
}
w.save("replica-byte-audit", report)
print(json.dumps(report, indent=2), flush=True)
