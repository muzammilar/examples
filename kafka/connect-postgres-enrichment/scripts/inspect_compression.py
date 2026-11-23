# /// script
# requires-python = ">=3.11"
# dependencies = ["lz4==4.4.5", "google-crc32c==1.9.0"]
# ///
"""Measure copied Kafka magic-2 record sections; never modify broker storage.

Layout and CRC coverage: Kafka 4.3 message-format documentation and
clients/.../record/internal/DefaultRecordBatch.java at tag 4.3.2.
https://kafka.apache.org/43/implementation/message-format/
https://github.com/apache/kafka/blob/4.3.2/clients/src/main/java/org/apache/kafka/common/record/internal/DefaultRecordBatch.java
Compression excludes the 61-byte batch header; physical data includes aborted
transactions, not only records visible to read_committed consumers.
"""

import io
import json
from pathlib import Path
import struct
import sys

import google_crc32c
import lz4.frame


def inspect(path):
    groups = {}
    with Path(path).open("rb") as stream:
        while prefix := stream.read(12):
            if len(prefix) != 12:
                raise ValueError("truncated batch prefix")
            offset, length = struct.unpack(">qi", prefix)
            if offset < 0 or not 49 <= length <= 128 * 1024 * 1024:
                raise ValueError("invalid or oversized batch")
            body = stream.read(length)
            if len(body) != length or body[4] != 2:
                raise ValueError("truncated batch or unsupported magic")
            if google_crc32c.value(body[9:]) != struct.unpack_from(">I", body, 5)[0]:
                raise ValueError("Kafka CRC32C mismatch")
            attributes = struct.unpack_from(">h", body, 9)[0]
            codec = attributes & 7
            if codec not in (0, 3):
                raise ValueError("expected uncompressed or LZ4 records")
            records = struct.unpack_from(">i", body, 45)[0]
            if records < 0:
                raise ValueError("negative record count")
            encoded = body[49:]
            decoded_bytes = len(encoded)
            if codec == 3:
                decoded_bytes = 0
                with lz4.frame.open(io.BytesIO(encoded), "rb") as decoder:
                    while block := decoder.read(1024 * 1024):
                        decoded_bytes += len(block)
                        if decoded_bytes > 128 * 1024 * 1024:
                            raise ValueError("decoded batch exceeds inspection budget")
            group = groups.setdefault(
                "control" if attributes & 32 else "data",
                {
                    "batches": 0,
                    "records": 0,
                    "compressed_bytes": 0,
                    "uncompressed_bytes": 0,
                    "batch_header_bytes": 0,
                    "codecs": [],
                },
            )
            group["batches"] += 1
            group["records"] += records
            group["compressed_bytes"] += len(encoded)
            group["uncompressed_bytes"] += decoded_bytes
            group["batch_header_bytes"] += 61
            if codec not in group["codecs"]:
                group["codecs"].append(codec)
    if not groups:
        raise ValueError("empty log segment")
    return groups


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: uv run --script inspect_compression.py SEGMENT.log ..."
        )
    print(json.dumps({name: inspect(name) for name in sys.argv[1:]}, indent=2))
