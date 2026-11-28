"""Native tablet agreement and direct body hashes of all published HDFS replicas."""

import json
import re
import lab


def check_doris(log):
    native = {r["query"]: r["rows"] for r in lab.records(log) if "query" in r}
    frontends, backends = native["SHOW FRONTENDS"], native["SHOW BACKENDS"]
    if (
        len(frontends) != 3
        or any(r["Alive"] != "true" or r["Role"] != "FOLLOWER" for r in frontends)
        or sum(r["IsMaster"] == "true" for r in frontends) != 1
    ):
        raise ValueError("Three live voting FEs and one leader required")
    if len(backends) != 3 or any(r["Alive"] != "true" for r in backends):
        raise ValueError("Three live BEs required")
    for table in ("orders", "regions"):
        groups = {}
        for row in native["SHOW TABLETS FROM pipeline." + table]:
            groups.setdefault(row["TabletId"], []).append(row)
        if len(groups) != 3 or any(
            len(rows) != 3
            or len({r["BackendId"] for r in rows}) != 3
            or len({r["Version"] for r in rows}) != 1
            or any(r["State"] != "NORMAL" for r in rows)
            for rows in groups.values()
        ):
            raise ValueError(
                "Three tablets with three current distinct replicas required"
            )
    return dict(voting_fes=3, live_bes=3, tablets=6, current_replicas=18)


def hdfs_replicas(path):
    text = lab.hdfs("fsck", "/lab/published", "-files", "-blocks", "-locations")
    (path / "published-fsck.log").write_text(text)
    if "Status: HEALTHY" not in text:
        raise ValueError("Published HDFS files unhealthy")
    expected = {}
    for line in text.splitlines():
        match = re.search(r"\b(blk_-?\d+)_\d+\s+len=(\d+)\s+Live_repl=(\d+)", line)
        if match:
            block, length, copies = match.groups()
            if int(copies) != 3:
                raise ValueError("Published block not RF3")
            expected[block] = int(length)
    if not expected:
        raise ValueError("No published blocks found")
    script = r"""import hashlib,json,re
from pathlib import Path
items=[]
for p in Path('/data/data').rglob('blk_*'):
 if p.is_file() and re.fullmatch(r'blk_-?\d+',p.name) and '/finalized/' in str(p):
  digest=hashlib.sha256()
  with p.open('rb') as f:
   for chunk in iter(lambda:f.read(1048576),b''):digest.update(chunk)
  items.append(dict(block=p.name,bytes=p.stat().st_size,sha256=digest.hexdigest()))
print(json.dumps(items))
"""
    bodies = {}
    for role in ("dn1", "dn2", "dn3"):
        identifier = lab.compose("ps", "--quiet", role).strip()
        lab.owned(identifier, role)
        records = json.loads(lab.compose("exec", "-T", role, "python3", "-c", script))
        lab.save(path / (role + "-blocks.json"), records)
        for record in records:
            if record["block"] in expected:
                bodies.setdefault(record["block"], []).append(
                    (role, record["bytes"], record["sha256"])
                )
    for block, length in expected.items():
        copies = bodies.get(block, [])
        if (
            len(copies) != 3
            or len({r[0] for r in copies}) != 3
            or {r[1] for r in copies} != {length}
            or len({r[2] for r in copies}) != 1
        ):
            raise ValueError("Wrong physical replicas for " + block)
    return dict(
        published_blocks=len(expected),
        physical_copies=3 * len(expected),
        bytes_read=3 * sum(expected.values()),
    )


def published_replication_restored(path=None):
    text = lab.hdfs("fsck", "/lab/published", "-files", "-blocks")
    if path is not None:
        import time

        with (path / "replication-probes.jsonl").open("a") as log:
            log.write(
                json.dumps({"observed_ns": time.time_ns(), "native_fsck": text}) + "\n"
            )
    counts = re.findall(r"Live_repl=(\d+)", text)
    return "Status: HEALTHY" in text and counts and all(int(n) == 3 for n in counts)
