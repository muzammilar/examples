"""Verify every deterministic order emitted by the distributed SQL job."""
import csv
import json
from pathlib import Path
import itertools
import zlib


def main():
    seen = bytearray(1_000_000)
    total = 0
    count = 0
    compressor = zlib.compressobj(wbits=31)
    encoded_bytes = 0
    gzip_bytes = 0
    paths = sorted(Path("/data/orders").rglob("part-*"))
    files = [path for path in paths if path.is_file()]
    for path in files:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                encoded_bytes += len(block)
                gzip_bytes += len(compressor.compress(block))
    gzip_bytes += len(compressor.flush())
    lines = itertools.chain.from_iterable(
        path.read_text().splitlines() for path in files
    )
    for row in csv.reader(lines):
        if len(row) != 3:
            raise ValueError(f"Unexpected row width {row!r}")
        order, customer, amount = map(int, row)
        if not 0 <= order < len(seen):
            raise ValueError(f"Out-of-range order {order}")
        if seen[order] or customer != order % 10000 or amount != 100 + order % 1000:
            raise ValueError(f"Duplicate or wrong order {row!r}")
        seen[order] = 1
        count += 1
        total += amount
    if count != len(seen):
        raise ValueError(f"Incomplete output {count} of {len(seen)}")
    print(json.dumps({"orders": count, "customers": 10000,
                      "amount_cents": total, "missing": 0, "wrong": 0,
                      "csv_bytes": encoded_bytes,
                      "allocated_output_bytes": sum(path.stat().st_blocks * 512 for path in files),
                      "output_codec": "uncompressed CSV",
                      "gzip_reference_bytes": gzip_bytes,
                      "gzip_reference_ratio": encoded_bytes / gzip_bytes}))


if __name__ == "__main__":
    main()
