"""Preserve bounded orphan candidates before deleting the only storage copy."""

import hashlib
import json
import os
from pathlib import Path
import sys
from verify import body, load

table = load(sys.argv[1])
report = json.loads(Path(sys.argv[2]).read_text())
candidates = report["candidate_unreferenced_data_files"]
if sum(candidates.values()) > 128 * 1024**2:
    raise ValueError(
        "Candidate evidence exceeds the bounded 128 MiB export; preserve the fixture"
    )
root = Path("/exports")
receipts = []
for uri, size in sorted(candidates.items()):
    value = body(table, uri)
    if len(value) != size:
        raise ValueError("Candidate size changed after writer fencing")
    name = hashlib.sha256(uri.encode()).hexdigest() + ".parquet"
    with (root / name).open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    receipts.append(
        dict(uri=uri, file=name, bytes=size, sha256=hashlib.sha256(value).hexdigest())
    )
with (root / "receipts.json").open("x") as stream:
    json.dump(receipts, stream, indent=2)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
print(
    json.dumps(
        dict(passed=True, exported_files=len(receipts), bytes=sum(candidates.values()))
    )
)
