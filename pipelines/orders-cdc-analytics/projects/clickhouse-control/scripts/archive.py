"""Preserve the exact consumed payloads/positions with bounded standard-library streaming."""

import gzip
import hashlib
import json
import os
import platform
import resource
import subprocess
import time


def preserve(root, parent, compose, path, fixture):
    expected = {}
    for folder in (root / "results").glob("source-*"):
        identity = folder / "fixture.json"
        if not identity.exists() or json.loads(identity.read_text())["id"] != fixture:
            continue
        for file in folder.glob("*-source-fence.txt"):
            for line in file.read_text().splitlines():
                if line.startswith("cdc.public."):
                    topic, partition, offset = line.split(":")
                    expected[(topic, int(partition))] = int(offset)
    if len(expected) != 5:
        raise RuntimeError("Exact five-partition source fence required")
    output = path / "consumed-cdc.jsonl.gz"
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    stream_hash = hashlib.sha256()
    unique_hash = hashlib.sha256()
    last = {}
    payloads = {}
    count = duplicates = uncompressed = 0
    query = "SELECT topic,partition,offset,payload FROM pipeline.cdc_history ORDER BY topic,partition,offset FORMAT JSONEachRow"
    with (path / "archive.stderr.log").open("xb") as error, output.open("xb") as file:
        child = subprocess.Popen(
            [
                *compose,
                "exec",
                "-T",
                "ch1",
                "clickhouse-client",
                "--user",
                "lab",
                "--password",
                "local-control-only",
                "--receive_timeout",
                "90",
                "--query",
                query,
            ],
            cwd=parent,
            stdout=subprocess.PIPE,
            stderr=error,
        )
        try:
            with gzip.GzipFile(fileobj=file, mode="wb", compresslevel=1) as packed:
                for line in child.stdout:
                    row = json.loads(line)
                    key = (row["topic"], int(row["partition"]))
                    offset = int(row["offset"])
                    if key not in expected or offset < 0 or offset >= expected[key]:
                        raise RuntimeError("Unwitnessed source position")
                    previous = last.get(key, -1)
                    if offset == previous:
                        if row["payload"] != payloads[key]:
                            raise RuntimeError(
                                "Conflicting payload for one Kafka position"
                            )
                        duplicates += 1
                    elif offset != previous + 1:
                        raise RuntimeError("Missing/out-of-order source position")
                    stream_hash.update(line)
                    if offset != previous:
                        unique_hash.update(line)
                    body = json.loads(row["payload"])
                    if body.get("op") not in ("c", "u", "d", "r"):
                        raise RuntimeError("Unsupported CDC operation")
                    last[key] = offset
                    payloads[key] = row["payload"]
                    count += 1
                    uncompressed += len(line)
                    packed.write(line)
            if child.wait(timeout=90) != 0:
                raise RuntimeError("Native source export failed; preserve stderr")
            file.flush()
            os.fsync(file.fileno())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=15)
    if any(last.get(key, -1) + 1 != end for key, end in expected.items()):
        raise RuntimeError("Incomplete source archive")
    digest = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    stat = output.stat()
    after = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "path": str(output.relative_to(root)),
        "sha256": digest.hexdigest(),
        "uncompressed_stream_sha256": stream_hash.hexdigest(),
        "unique_position_stream_sha256": unique_hash.hexdigest(),
        "records": count,
        "duplicate_positions": duplicates,
        "unique_positions": sum(expected.values()),
        "partition_fence": [
            {"topic": key[0], "partition": key[1], "end_offset": end}
            for key, end in sorted(expected.items())
        ],
        "codec": "gzip-level-1",
        "uncompressed_bytes": uncompressed,
        "compressed_bytes": stat.st_size,
        "allocated_bytes": stat.st_blocks * 512,
        "duration_s": time.monotonic() - started,
        "host_cpu_user_s": after.ru_utime - before.ru_utime,
        "host_cpu_system_s": after.ru_stime - before.ru_stime,
        "host_process_peak_rss_bytes": after.ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "scope": "source evidence copy with exact payload/offset continuity, not a qualified database backup/restore; host process peak spans its lifetime",
    }
    (path / "source-archive.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
