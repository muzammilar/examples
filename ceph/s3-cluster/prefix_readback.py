"""Verify a finite external ACK prefix while both traffic roles continue."""

import concurrent.futures
import time

import workload as w

store = w.Store()
values, required = store.expected(fault=True)
if not required:
    raise RuntimeError("missing witnessed read prefix")
started = time.monotonic()
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
    list(pool.map(store.get, [values[key] for key in sorted(required)]))
w.save(
    "prefix-readback",
    {
        "objects": len(required),
        "bytes": sum(values[k]["size"] for k in required),
        "seconds": time.monotonic() - started,
    },
)
