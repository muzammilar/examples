"""Native peer/disk recovery with live read/write observations and a frozen ACK barrier."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

from live_observer import LiveObserver
from production_observations import timestamp

ROOT = Path("results")
project = os.environ["COMPOSE_PROJECT_NAME"]
compose = ["docker", "compose"]
hosts = ["q0", "q1", "q2"]
collections = ["float-ram", "float-disk", "scalar8", "turbo4"]
mode = sys.argv[1]
if mode not in ("process", "write-leader", "disk", "recover"):
    raise ValueError(mode)
case = mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def run(*args, capture=False, timeout=300):
    result = subprocess.run(
        list(args), text=True, capture_output=capture, check=False, timeout=timeout
    )
    if result.returncode:
        if capture:
            with (ROOT / (case + "-commands.log")).open("a") as log:
                log.write(result.stdout + result.stderr)
            raise RuntimeError(f"{args}: {result.stderr[-5000:]}")
        result.check_returncode()
    return result.stdout


def tool(action, *args, capture=False):
    return run(
        *compose, "run", "--rm", "-T", "vector-workload", action, *args, capture=capture
    )


def api(host, method, path, body=None):
    value = json.loads(tool("api", host, method, path, json.dumps(body), capture=True))
    if value.get("status") != "ok":
        raise RuntimeError(str(value))
    return value["result"]


def live_host():
    for host in hosts:
        cid = run(*compose, "ps", "-q", host, capture=True).strip()
        if cid:
            info = json.loads(run("docker", "inspect", cid, capture=True))[0]
            if info["State"]["Running"]:
                return host
    raise RuntimeError("no owned peer is running")


def wait(predicate, description, timeout=300):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            if predicate():
                return
        except RuntimeError as error:
            last = str(error)
        time.sleep(2)
    raise RuntimeError(f"timed out: {description}; last error: {last}")


def native_ready():
    def healthy():
        for host in hosts:
            graph = api(host, "GET", "/cluster")
            if len(graph["peers"]) != 3 or graph["raft_info"].get("leader") is None:
                return False
            for name in collections:
                info = api(host, "GET", f"/collections/{name}/cluster")
                if (
                    len(info["local_shards"]) != 3
                    or info["shard_transfers"]
                    or any(s["state"] != "Active" for s in info["local_shards"])
                ):
                    return False
        return True

    wait(
        healthy,
        "three peers and all three physical copies per customer shard",
        timeout=600,
    )


def journal():
    path = ROOT / ("fault-" + case + ".jsonl")
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(after):
    def active():
        if load.poll() is not None:
            raise RuntimeError("writer exited before native repair")
        return (
            sum(r["stage"] == "ack" and timestamp(r["at"]) > after for r in journal())
            >= 3
        )

    wait(active, "fresh acknowledged writes", timeout=180)
    observer.progress()


def ack_barrier():
    frozen = {}
    for event in journal():
        if event["stage"] == "ack":
            frozen[event["shard_key"]] = event["ids"]
    if len(frozen) != 3:
        raise RuntimeError("no witnessed ACKs for every customer group")
    (ROOT / (case + "-ack-barrier.json")).write_text(
        json.dumps(frozen, indent=2) + "\n"
    )

    def caught_up():
        for host in hosts:
            info = api(host, "GET", "/collections/turbo4/cluster")
            for shard in info["local_shards"]:
                expected = set(frozen[shard["shard_key"]])
                points = api(
                    host,
                    "POST",
                    f"/collections/turbo4/shards/{shard['shard_id']}/points",
                    {
                        "ids": sorted(expected),
                        "with_payload": True,
                        "with_vector": False,
                    },
                )
                if {point["id"] for point in points} != expected:
                    return False
        return True

    wait(caught_up, "frozen acknowledged IDs physically present on every replica")


def recover_disk():
    marker = ROOT / "pending-replacement.json"
    pending = json.loads(marker.read_text())
    target, old = pending["target"], pending["old_peer_id"]
    if target != "q2" or not pending.get("volume_removed"):
        raise RuntimeError("replacement marker does not prove an owned empty disk")
    source = live_host()
    # Remove each lost owner through native metadata; RF3 and write quorum 2
    # remain configured throughout. Surviving copies are never reseeded.
    for name in collections:
        info = api(source, "GET", f"/collections/{name}/cluster")
        for shard in info["remote_shards"]:
            if shard["peer_id"] == old:
                api(
                    source,
                    "POST",
                    f"/collections/{name}/cluster",
                    {"drop_replica": {"shard_id": shard["shard_id"], "peer_id": old}},
                )
    graph = api(source, "GET", "/cluster")
    if str(old) in graph["peers"]:
        api(source, "DELETE", f"/cluster/peer/{old}")
    run(*compose, "up", "-d", "--no-deps", target)

    def joined():
        graph = api(source, "GET", "/cluster")
        return len(graph["peers"]) == 3 and any(
            urlsplit(p["uri"]).hostname == target for p in graph["peers"].values()
        )

    wait(joined, "empty replacement joined Raft")
    graph = api(source, "GET", "/cluster")
    new = next(
        int(key)
        for key, value in graph["peers"].items()
        if urlsplit(value["uri"]).hostname == target
    )
    if new == old:
        raise RuntimeError("replacement reused the lost disk peer identity")
    for name in collections:
        source_info = api(source, "GET", f"/collections/{name}/cluster")
        target_info = api(target, "GET", f"/collections/{name}/cluster")
        assigned = {s["shard_id"] for s in target_info["local_shards"]}
        for shard in source_info["local_shards"]:
            if shard["shard_id"] not in assigned:
                api(
                    source,
                    "POST",
                    f"/collections/{name}/cluster",
                    {
                        "replicate_shard": {
                            "shard_id": shard["shard_id"],
                            "from_peer_id": source_info["peer_id"],
                            "to_peer_id": new,
                            "method": "snapshot",
                        }
                    },
                )
    native_ready()
    return old, new


if mode == "recover":
    recover_disk()
    tool("verify")
    (ROOT / "pending-replacement.json").unlink()
    raise SystemExit(0)
native_ready()
graph = api("q0", "GET", "/cluster")
if mode == "process":
    peer = graph["raft_info"]["leader"]
elif mode == "write-leader":
    # With RF3 on three peers, the Medium ordering leader is the maximum active
    # replica ID for every shard (pinned replica_set/update.rs).
    peer = max(int(key) for key in graph["peers"])
else:
    peer = next(
        int(key)
        for key, value in graph["peers"].items()
        if urlsplit(value["uri"]).hostname == "q2"
    )
target = urlsplit(graph["peers"][str(peer)]["uri"]).hostname
if os.environ.get("FAULT_SERVICE"):
    if mode != "process" or os.environ["FAULT_SERVICE"] != "q1":
        raise ValueError("alternate process target must be q1")
    target = "q1"
    peer = next(
        int(key)
        for key, value in graph["peers"].items()
        if urlsplit(value["uri"]).hostname == target
    )
if target not in hosts:
    raise RuntimeError("foreign native peer URI")
cid = run(*compose, "ps", "-q", target, capture=True).strip()
info = json.loads(run("docker", "inspect", cid, capture=True))[0]
if (
    info["Config"]["Labels"].get("com.docker.compose.project") != project
    or info["Config"]["Labels"].get("com.docker.compose.service") != target
):
    raise RuntimeError("foreign failure target")
survivors = {}
for host in hosts:
    if host == target:
        continue
    identity = json.loads(
        run(
            "docker",
            "inspect",
            run(*compose, "ps", "-q", host, capture=True).strip(),
            capture=True,
        )
    )[0]
    if identity["Config"]["Labels"].get("com.docker.compose.project") != project:
        raise RuntimeError("foreign survivor")
    survivors[host] = {
        "id": identity["Id"],
        "started_at": identity["State"]["StartedAt"],
    }
volume = None
if mode == "disk":
    volume = next(
        v["Name"]
        for v in info["Mounts"]
        if v["Type"] == "volume" and v["Destination"] == "/qdrant/storage"
    )
    disk = json.loads(run("docker", "volume", "inspect", volume, capture=True))[0]
    if (
        disk["Labels"].get("com.docker.compose.project") != project
        or disk["Labels"].get("com.docker.compose.volume") != target
    ):
        raise RuntimeError("foreign disk")
observer = LiveObserver(
    case, [*compose, "run", "--rm", "-e", "FAULT_CASE=" + case, "reader"], ROOT
)
log = (ROOT / (case + "-writer.log")).open("w")
load_name = project + "-" + case + "-writer"
load = subprocess.Popen(
    [
        *compose,
        "run",
        "--rm",
        "--name",
        load_name,
        "-e",
        "FAULT_CASE=" + case,
        "writer",
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
)
replacement = None
try:
    progress(observer.markers[0][1])
    began = time.monotonic()
    observer.mark("outage")
    run(*compose, "kill", "--signal", "SIGKILL", target)
    stopped = json.loads(run("docker", "inspect", cid, capture=True))[0]
    if stopped["State"]["Running"]:
        raise RuntimeError("old process was not fenced")
    if volume:
        pending = {
            "target": target,
            "old_peer_id": peer,
            "volume": volume,
            "volume_removed": False,
        }
        (ROOT / "pending-replacement.json").write_text(
            json.dumps(pending, indent=2) + "\n"
        )
        run(*compose, "rm", "--force", target)
        run("docker", "volume", "rm", volume)
        pending["volume_removed"] = True
        (ROOT / "pending-replacement.json").write_text(
            json.dumps(pending, indent=2) + "\n"
        )
    time.sleep(30)
    # Require progress beyond in-flight requests finishing just after SIGKILL.
    progress(observer.markers[-1][1] + timedelta(seconds=10))
    observer.mark("recovery")
    if volume:
        replacement = recover_disk()
    else:
        run(*compose, "up", "-d", "--no-deps", target)
        native_ready()
    ack_barrier()
    for host, previous in survivors.items():
        current = json.loads(run("docker", "inspect", previous["id"], capture=True))[0]
        if (
            not current["State"]["Running"]
            or current["State"]["StartedAt"] != previous["started_at"]
        ):
            raise RuntimeError("surviving peer restarted during recovery: " + host)
    recovered = time.monotonic() - began
    observer.mark("after")
    progress(observer.markers[-1][1])
    (ROOT / ("stop-" + case)).touch()
    if load.wait(timeout=60):
        raise RuntimeError("writer failed")
    observations = observer.finish()
    tool("verify-recovery")
    if volume:
        (ROOT / "pending-replacement.json").unlink()
    report = {
        "case": case,
        "target": target,
        "old_peer_id": peer,
        "replacement_peer_ids": replacement,
        "removed_volume": volume,
        "survivors_unchanged": survivors,
        "recovery_seconds_including_30s_hold": recovered,
        "observations": observations,
        "reconciliation": json.loads((ROOT / "recovery-history.json").read_text()),
    }
    (ROOT / (case + "-result.json")).write_text(json.dumps(report, indent=2) + "\n")
except Exception as error:
    (ROOT / (case + "-failure.json")).write_text(
        json.dumps({"case": case, "error": str(error)}, indent=2) + "\n"
    )
    raise
finally:
    if not volume:
        run(*compose, "up", "-d", "--no-deps", target)
    (ROOT / ("stop-" + case)).touch()
    observer.close()
    try:
        load.wait(timeout=30)
    except subprocess.TimeoutExpired:
        run("docker", "stop", "--timeout", "5", load_name)
        load.wait(timeout=30)
    log.close()
    with (ROOT / (case + "-peers.log")).open("w") as output:
        subprocess.run(
            [*compose, "logs", "--tail", "60"],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
