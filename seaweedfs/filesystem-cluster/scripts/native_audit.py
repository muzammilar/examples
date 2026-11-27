"""Independent payload oracle and direct reads of three native zone copies."""

import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


def get(url):
    with urlopen(url, timeout=15) as response:
        return response.read()


def main():
    fixture = json.loads(Path("/results/fixture.json").read_text())
    locations = {}
    for node in range(3):
        status = json.loads(get(f"http://volume{node}:8080/status?pretty=y"))
        for volume in status.get("Volumes") or []:
            locations.setdefault(str(volume["Id"]), []).append(node)
    counts = {
        "files": 0,
        "logical_bytes": 0,
        "direct_replica_reads": 0,
        "physical_body_bytes": 0,
        "acks": 0,
        "ambiguous_present": 0,
        "ambiguous_absent": 0,
    }
    corpora = [
        (run, Path("/results/journals") / (run + ".jsonl"), "")
        for run in fixture["runs"]
    ]
    http_path = Path("/results/native-http/fixture.json")
    if http_path.exists():
        http = json.loads(http_path.read_text())
        if http["parent_fixture"] != fixture["id"]:
            raise RuntimeError("HTTP corpus belongs to another lab fixture")
        corpora.extend(
            (
                run,
                Path("/results/native-http/journals") / (run + ".jsonl"),
                "native-http/",
            )
            for run in http["runs"]
        )
    for run, path, prefix in corpora:
        raw = path.read_bytes()
        if not raw or not raw.endswith(b"\n"):
            raise RuntimeError("Incomplete external journal")
        attempts, outcomes, acks = {}, {}, {}
        for row in map(json.loads, raw.splitlines()):
            seq = row["sequence"]
            if (
                type(seq) is not int
                or seq < 0
                or row["run"] != run
                or not 1 <= row["bytes"] <= 1048576
            ):
                raise RuntimeError("Invalid witness identity/size")
            expected = hashlib.sha256(
                hashlib.shake_256(f"95513:{run}:{seq}".encode()).digest(row["bytes"])
            ).hexdigest()
            if row["sha256"] != expected:
                raise RuntimeError("Witness differs from independent payload oracle")
            if row["event"] == "attempt" and seq not in attempts:
                attempts[seq] = row
            elif (
                row["event"] in ("ack", "ambiguous")
                and seq in attempts
                and seq not in outcomes
            ):
                if any(row[k] != attempts[seq][k] for k in ("sha256", "bytes", "run")):
                    raise RuntimeError("ACK differs from intent")
                outcomes[seq] = row["event"]
                if row["event"] == "ack":
                    acks[seq] = row
            else:
                raise RuntimeError(
                    "Duplicate or misordered outcome; preserve and reconcile manually"
                )
        if not acks:
            raise RuntimeError("No acknowledged progress in this run")
        counts["acks"] += len(acks)
        for seq, row in attempts.items():
            observation = Path("/results/observed-publications") / (
                prefix.replace("/", "-") + run + "-" + str(seq) + ".json"
            )
            try:
                metadata = json.loads(
                    get(f"http://filer0:8888/{prefix}{run}/{seq}?metadata=true")
                )
            except HTTPError as error:
                if error.code != 404 or seq in acks or observation.exists():
                    raise
                try:
                    metadata = json.loads(
                        get(f"http://filer1:8888/{prefix}{run}/{seq}?metadata=true")
                    )
                except HTTPError as second:
                    if second.code != 404:
                        raise
                    counts["ambiguous_absent"] += 1
                    continue
            if int(metadata["FileSize"]) != row["bytes"]:
                raise RuntimeError("Filer size differs from witness")
            chunks = sorted(
                metadata.get("chunks") or [], key=lambda v: int(v.get("offset", 0))
            )
            if not chunks:
                raise RuntimeError(
                    "This bounded large-file corpus requires direct chunks"
                )
            digests = [hashlib.sha256() for _ in range(3)]
            offset = 0
            for chunk in chunks:
                if (
                    chunk.get("is_chunk_manifest")
                    or chunk.get("cipher_key")
                    or int(chunk.get("offset", 0)) != offset
                ):
                    raise RuntimeError("Unexpected chunk layout")
                fid = chunk["file_id"]
                if set(locations[fid.split(",")[0]]) != {0, 1, 2}:
                    raise RuntimeError("Not three zone owners")
                for node in range(3):
                    body = get(
                        f'http://volume{node}:8080/{quote(fid,safe=",")}?proxied=true'
                    )
                    if len(body) != int(chunk["size"]):
                        raise RuntimeError("Direct replica size mismatch")
                    digests[node].update(body)
                    counts["direct_replica_reads"] += 1
                    counts["physical_body_bytes"] += len(body)
                offset += int(chunk["size"])
            if offset != row["bytes"] or any(
                d.hexdigest() != row["sha256"] for d in digests
            ):
                raise RuntimeError("Native replica body mismatch")
            counts["files"] += 1
            counts["logical_bytes"] += row["bytes"]
            if seq not in acks:
                counts["ambiguous_present"] += 1
                expected = {k: row[k] for k in ("run", "sequence", "bytes", "sha256")}
                if observation.exists():
                    if json.loads(observation.read_text()) != expected:
                        raise RuntimeError("Observed publication differs from original")
                else:
                    observation.parent.mkdir(exist_ok=True)
                    with observation.open("x") as stream:
                        stream.write(json.dumps(expected) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
