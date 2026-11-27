"""Require live voting members and three current replica records per tablet."""

import csv
import io
import json
from pathlib import Path
import time

import kube


def query(sql):
    return list(csv.DictReader(io.StringIO(kube.query(sql).stdout), delimiter="\t"))


def main():
    frontends = query("SHOW FRONTENDS")
    backends = query("SHOW BACKENDS")
    replicas = query("SHOW TABLETS FROM lab.orders")
    Path("results").mkdir(exist_ok=True)
    observed = {"frontends": frontends, "backends": backends, "replicas": replicas}
    (Path("results") / ("native-topology-" + str(time.time_ns()) + ".json")).write_text(
        json.dumps(observed, indent=2) + "\n"
    )
    if len(frontends) != 3 or any(
        f["Alive"].lower() != "true" or f["Role"] != "FOLLOWER" for f in frontends
    ):
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
        if any(
            r["State"] != "NORMAL" or int(r["LstFailedVersion"]) > 0 for r in copies
        ):
            raise ValueError(f"Unhealthy replica for tablet {tablet}")
    pods = kube.get("pods")["items"]
    for role in ("frontend", "backend"):
        members = [
            p
            for p in pods
            if p["metadata"].get("labels", {}).get("lab.example/role") == role
        ]
        if len(members) != 3 or len({p["spec"]["nodeName"] for p in members}) != 3:
            raise ValueError("Each role must occupy three distinct owned workers")
        if any(
            not any("persistentVolumeClaim" in v for v in p["spec"].get("volumes", []))
            for p in members
        ):
            raise ValueError("Every database member requires a persistent volume")
    print(
        json.dumps(
            {
                "voting_frontends": 3,
                "leaders": 1,
                "backends": 3,
                "tablets": 8,
                "replica_records": len(replicas),
                "copies_per_tablet": 3,
                "versions_agree": True,
            }
        )
    )


if __name__ == "__main__":
    main()
