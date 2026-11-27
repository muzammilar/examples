"""Native membership and tablet agreement; manifests alone do not prove RF3."""

import csv
import io
import json

import lab


def query(sql):
    raw = lab.command(
        "exec",
        "-T",
        "fe1",
        "mysql",
        "--batch",
        "--raw",
        "-h127.0.0.1",
        "-P9030",
        "-uroot",
        "-e",
        sql,
    )
    return raw, list(csv.DictReader(io.StringIO(raw), delimiter="\t"))


def verify(path):
    raw, frontends = query("SHOW FRONTENDS")
    (path / "frontends.tsv").write_text(raw)
    if (
        len(frontends) != 3
        or any(row["Alive"] != "true" or row["Role"] != "FOLLOWER" for row in frontends)
        or sum(row["IsMaster"] == "true" for row in frontends) != 1
    ):
        raise RuntimeError("Three native voting FEs and one leader required")
    raw, backends = query("SHOW BACKENDS")
    (path / "backends.tsv").write_text(raw)
    if len(backends) != 3 or any(row["Alive"] != "true" for row in backends):
        raise RuntimeError("Three native backends required")
    result = {"voting_frontends": 3, "backends": 3, "tables": {}}
    for table in ("checkpointed", "asynchronous"):
        raw, replicas = query("SHOW TABLETS FROM pipeline." + table)
        (path / (table + "-replicas.tsv")).write_text(raw)
        groups = {}
        for row in replicas:
            groups.setdefault(row["TabletId"], []).append(row)
        if len(groups) != 8 or any(
            len(rows) != 3
            or len({row["BackendId"] for row in rows}) != 3
            or len({row["Version"] for row in rows}) != 1
            or any(row["State"] != "NORMAL" for row in rows)
            for rows in groups.values()
        ):
            raise RuntimeError(
                "Eight tablets with three distinct current NORMAL replicas required"
            )
        result["tables"][table] = {"tablets": len(groups), "replicas": len(replicas)}
    lab.save(path / "topology-verdict.json", result)
    return result


if __name__ == "__main__":
    path = lab.directory("topology")
    print(json.dumps(verify(path)))
