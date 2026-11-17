"""Strict interpretation of the pinned native membership and repair schemas."""

from uuid import UUID

NODES = {f"rustfs{i}" for i in range(4)}


def uuid(value):
    if str(UUID(value)) != value:
        raise RuntimeError("noncanonical native UUID")
    return value


def member_identity(views, *, repaired=False):
    if set(views) != NODES:
        raise RuntimeError("missing native member observation")
    identities = []
    for view in views.values():
        if view["bitrotSelftest"] != "passed":
            raise RuntimeError("native bitrot self-test is not verified")
        ready = view["ready"]
        if (
            ready["ready"] is not True
            or not all(
                ready["details"][k]["ready"] is True
                for k in ["storage", "lock", "iam", "poolMetadata"]
            )
            or ready["details"]["storage"]["readQuorum"] is not True
            or ready["details"]["storage"]["writeQuorum"] is not True
        ):
            return None
        info = view["info"]
        backend = info["backend"]
        if any(
            backend[key] != expected
            for key, expected in {
                "backendType": "Erasure",
                "totalSets": [1],
                "totalDrivesPerSet": [4],
                "standardSCParity": 2,
            }.items()
        ):
            raise RuntimeError(
                "native erasure layout differs from EC2+2 / one four-drive set"
            )
        if (
            backend["onlineDisks"],
            backend["offlineDisks"],
            backend["unknownDisks"],
        ) != (4, 0, 0):
            return None
        protection = view["integrity"]
        if not all(
            protection[key] is True
            for key in [
                "local_reader_supported",
                "write_requested",
                "fleet_operator_attested",
                "new_writes_enabled_here",
            ]
        ):
            raise RuntimeError("protected native writes/readers are not enabled")
        # Fleet attestation is explicit; the API correctly does not claim that
        # our same-host lab establishes all deployment/failure prerequisites.
        servers = {s["endpoint"]: s for s in info["servers"]}
        if (
            set(servers) != {f"rustfs{i}:9000" for i in range(4)}
            or len(info["servers"]) != 4
        ):
            return None
        disks = {}
        for i in range(4):
            server = servers[f"rustfs{i}:9000"]
            if server["state"] != "online" or len(server["drives"]) != 1:
                return None
            disk = server["drives"][0]
            if disk["state"] != "ok":
                return None
            if (disk["pool_index"], disk["set_index"], disk["disk_index"]) != (0, 0, i):
                raise RuntimeError("native endpoint assigned to wrong pool/set/slot")
            expected = f"http://rustfs{i}:9000/data/disk0"
            if disk["endpoint"] != expected or disk["path"] != "/data/disk0":
                raise RuntimeError(
                    "native drive endpoint/path differs from owned mount"
                )
            if repaired and disk["healing"]:
                return None
            if not disk["uuid"]:
                return None  # Connected filesystem; not yet a formatted quorum member.
            disks[f"rustfs{i}"] = uuid(disk["uuid"])
        if len(set(disks.values())) != 4:
            raise RuntimeError("duplicate native disk identity")
        if repaired and info["pools"]["0"]["0"]["healDisks"] != 0:
            return None
        identities.append({"deployment": uuid(info["deploymentID"]), "disks": disks})
    if any(v != identities[0] for v in identities):
        return None
    return identities[0]


def replacement_complete(value, *, endpoint=None, previous=()):
    if value["contractVersion"] != 2 or value["scope"] != "cluster_survivor_disks":
        raise RuntimeError("unsupported native replacement-status contract")
    cluster = value["cluster"]
    local = value["localDiagnostics"]
    if (
        cluster["definitive"] is not True
        or cluster["reason"] is not None
        or value["localBlocked"]
        or local["definitive"] is not True
        or local["rootBlocked"]
        or local["checkpointBlocked"]
    ):
        return False
    records = cluster["records"]
    for record in records:
        if (
            record["state"] != "completed"
            or record["verifiedAt"] is None
            or record["reason"] is not None
            or record["generation"] != record["taskId"]
            or not record["setDiskId"]
            or not record["targetSlots"]
        ):
            return False
    if endpoint is not None:
        return any(
            r["taskId"] not in previous and r["targetSlots"] == [endpoint]
            for r in records
        )
    return True


def heal_complete(result, *, minimum_objects=0):
    outcome = result.get("outcome")
    if not outcome:
        if result.get("summary") in {"running", "queued", "starting"}:
            return False
        raise RuntimeError("native heal omitted canonical coverage/counters")
    state = outcome["execution"]["state"]
    if state in ["pending", "running"]:
        return False
    if state != "completed" or outcome["coverage"] != "complete":
        raise RuntimeError("native heal failed or has partial coverage")
    counts = outcome["counters"]
    if (
        any(counts[key] != 0 for key in ["failed", "skipped", "unknown"])
        or counts["overflowed"] is not False
    ):
        raise RuntimeError("native heal contains unresolved/failed objects")
    if counts["processed"] != counts["healed"] + counts["unchanged"]:
        raise RuntimeError(
            "native canonical heal counters do not partition processed items"
        )
    if counts["processed"] < minimum_objects:
        raise RuntimeError(
            "native scan processed fewer items than the fixed ACK prefix"
        )
    # unchanged includes authoritative absence/metadata dispositions. It is not
    # an independent all-shard byte count; external GETs and raw fingerprints
    # remain separate gates.
    return True
