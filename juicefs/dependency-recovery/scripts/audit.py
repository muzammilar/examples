"""Independent ACK bodies and native metadata→slice→three-zone object checks."""

import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import quote
from urllib.request import urlopen


def get(url):
    with urlopen(url, timeout=15) as response:
        return response.read()


def journal(path, run):
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise ValueError("Truncated/empty witness")
    attempts, acks, ambiguous = {}, {}, {}
    for line in raw.splitlines():
        r = json.loads(line)
        if (
            r["run"] != run
            or type(r["sequence"]) is not int
            or not 0 <= r["sequence"] < 1024
            or not 1 <= r["bytes"] <= 1048576
        ):
            raise ValueError("Invalid witness shape")
        expected = hashlib.shake_256(f"96517:{run}:{r['sequence']}".encode()).digest(
            r["bytes"]
        )
        if hashlib.sha256(expected).hexdigest() != r["sha256"]:
            raise ValueError("Witness differs from independent corpus")
        target = {"attempt": attempts, "ack": acks, "ambiguous": ambiguous}.get(
            r["event"]
        )
        if target is None or r["sequence"] in target:
            raise ValueError("Duplicate/unknown witness event")
        target[r["sequence"]] = r
    if (
        not acks
        or set(acks) & set(ambiguous)
        or set(attempts) != set(acks) | set(ambiguous)
    ):
        raise ValueError("Unreconciled attempts")
    for seq, r in {**acks, **ambiguous}.items():
        if (
            r["bytes"] != attempts[seq]["bytes"]
            or r["sha256"] != attempts[seq]["sha256"]
        ):
            raise ValueError("Outcome differs from intent")
    return attempts, acks, ambiguous


def object_key(entry):
    chunks = entry.get("chunks") or []
    if len(chunks) != 1 or chunks[0]["index"] != 0 or len(chunks[0]["slices"]) != 1:
        raise ValueError("Expected one bounded contiguous slice")
    s = chunks[0]["slices"][0]
    if (
        s.get("pos", 0) != 0
        or s.get("off", 0) != 0
        or s["len"] != s["size"]
        or s["size"] != entry["attr"]["length"]
    ):
        raise ValueError("Unexpected slice boundaries")
    ident = s["id"]
    if type(ident) is not int or ident < 1:
        raise ValueError("Invalid slice identity")
    return f"juicefs-lab/chunks/{ident//1000000}/{ident//1000}/{ident}_0_{s['size']}"


def copies(key, expected_sha, size):
    meta = json.loads(
        get(
            "http://filer1:8888/buckets/juicefs/"
            + quote(key, safe="/")
            + "?metadata=true"
        )
    )
    chunks = sorted(meta.get("chunks") or [], key=lambda c: int(c.get("offset", 0)))
    if not chunks:
        raise ValueError("Expected direct object chunks")
    digests = [hashlib.sha256() for _ in range(3)]
    offset = 0
    for chunk in chunks:
        if (
            chunk.get("is_chunk_manifest")
            or chunk.get("cipher_key")
            or int(chunk.get("offset", 0)) != offset
        ):
            raise ValueError("Unsupported native object layout")
        fid = chunk["file_id"]
        owners = json.loads(
            get("http://master1:9333/dir/lookup?volumeId=" + fid.split(",")[0])
        )["locations"]
        if {o["url"] for o in owners} != {f"volume{i}:8080" for i in range(1, 4)}:
            raise ValueError("Not three native volume owners")
        for i in range(3):
            value = get(f"http://volume{i+1}:8080/{quote(fid,safe=',')}?proxied=true")
            if len(value) != int(chunk["size"]):
                raise ValueError("Native length mismatch")
            digests[i].update(value)
        offset += int(chunk["size"])
    if offset != size or any(d.hexdigest() != expected_sha for d in digests):
        raise ValueError("Native replica body mismatch")
    return len(chunks) * 3, offset * 3


def main():
    fixture = json.loads(Path("/results/fixture.json").read_text())
    dump = json.loads(Path(sys.argv[1]).read_text())
    root = dump["FSTree"]["entries"]
    counts = dict(
        acks=0,
        ambiguous=0,
        logical_bytes=0,
        native_replica_reads=0,
        native_replica_bytes=0,
    )
    ambiguous_states = []
    for run in fixture["runs"]:
        attempts, acks, ambiguous = journal(
            Path("/results/journals") / (run + ".jsonl"), run
        )
        expected_count = (
            fixture["files"] if run == fixture["runs"][0] else fixture["live_files"]
        )
        if len(attempts) != expected_count or any(
            r["bytes"] != fixture["payload_bytes"] for r in attempts.values()
        ):
            raise ValueError("Incomplete/changed declared source corpus")
        entries = root[run].get("entries") or {}
        for seq, r in acks.items():
            entry = entries.get(str(seq))
            if not entry or entry["attr"]["length"] != r["bytes"]:
                raise ValueError("ACK absent/wrong in native metadata")
            n, b = copies(object_key(entry), r["sha256"], r["bytes"])
            counts["native_replica_reads"] += n
            counts["native_replica_bytes"] += b
            counts["acks"] += 1
            counts["logical_bytes"] += r["bytes"]
        allowed = {str(seq) for seq in acks}
        for seq in ambiguous:
            allowed |= {str(seq), "." + str(seq) + ".tmp"}
            present = [
                name for name in (str(seq), "." + str(seq) + ".tmp") if name in entries
            ]
            if len(present) > 1:
                raise ValueError(
                    "Both staging and final name exist for a single non-replayed publication"
                )
            state = dict(run=run, sequence=seq, present=present)
            if present:
                entry = entries[present[0]]
                size = entry["attr"]["length"]
                state["bytes"] = size
                if size == ambiguous[seq]["bytes"]:
                    n, b = copies(object_key(entry), ambiguous[seq]["sha256"], size)
                    state.update(
                        complete_body=True,
                        native_replica_reads=n,
                        native_replica_bytes=b,
                    )
                elif size == 0:
                    state.update(
                        complete_body=False, observed_state="empty unacknowledged file"
                    )
                else:
                    raise ValueError(
                        "Nonempty partial ambiguous file needs explicit body reconciliation before cleanup"
                    )
            ambiguous_states.append(state)
        if set(entries) - allowed:
            raise ValueError("Unexpected namespace entry")
        counts["ambiguous"] += len(ambiguous)
    print(
        json.dumps(
            dict(
                passed=True,
                **counts,
                ambiguous_states=ambiguous_states,
                compression="none",
                measured_data_ratio=1.0,
                note="Every ACK slice has identical logical and native stored bytes; metadata/WAL/replicas are separate amplification",
            )
        )
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        print(
            json.dumps(
                dict(
                    tool_counters={
                        name: (Path("/sys/fs/cgroup") / name).read_text()
                        for name in (
                            "cpu.stat",
                            "memory.current",
                            "memory.peak",
                            "memory.events",
                            "io.stat",
                        )
                    }
                )
            ),
            flush=True,
        )
