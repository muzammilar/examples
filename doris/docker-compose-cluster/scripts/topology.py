"""Require live voting members and three current replica records per tablet."""
import csv
import io
import json
from pathlib import Path
import subprocess

COMPOSE = ["docker", "compose", "--project-name", "doris-replicated-orders"]


def query(sql):
    text = subprocess.check_output(
        [*COMPOSE, "exec", "-T", "fe1", "mysql", "--batch", "--raw", "-h127.0.0.1", "-P9030", "-uroot", "-e", sql], text=True,
    )
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def main():
    frontends = query("SHOW FRONTENDS")
    backends = query("SHOW BACKENDS")
    replicas = query("SHOW TABLETS FROM lab.orders")
    Path("results").mkdir(exist_ok=True)
    observed = {"frontends": frontends, "backends": backends, "replicas": replicas}
    Path("results/native-topology.json").write_text(json.dumps(observed, indent=2) + "\n")
    if len(frontends) != 3 or any(f["Alive"].lower() != "true" or f["Role"] != "FOLLOWER" for f in frontends):
        raise ValueError("Expected three live voting FE members")
    if sum(f["IsMaster"].lower() == "true" for f in frontends) != 1:
        raise ValueError("Expected exactly one FE leader")
    if len(backends) != 3 or any(b["Alive"].lower() != "true" for b in backends):
        raise ValueError("Expected three live backends")
    backend_ids = {b["BackendId"] for b in backends}
    tablets = {}
    for replica in replicas:
        tablets.setdefault(replica["TabletId"], []).append(replica)
    if len(tablets) != 8:
        raise ValueError(f"Expected eight tablets, got {len(tablets)}")
    for tablet, copies in tablets.items():
        if len(copies) != 3 or {r["BackendId"] for r in copies} != backend_ids:
            raise ValueError(f"Wrong owners for tablet {tablet}")
        if len({r["Version"] for r in copies}) != 1:
            raise ValueError(f"Replica version disagreement for tablet {tablet}")
        if any(r["State"] != "NORMAL" or int(r["LstFailedVersion"]) > 0 for r in copies):
            raise ValueError(f"Unhealthy replica for tablet {tablet}")
    print(json.dumps({"voting_frontends": 3, "leaders": 1, "backends": 3,
                      "tablets": 8, "replica_records": len(replicas),
                      "copies_per_tablet": 3, "versions_agree": True}))


if __name__ == "__main__":
    main()
