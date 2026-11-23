"""Owned fixture setup, retained-state validation, and evidence-preserving teardown."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid

import enrichment_control as control
import enrichment_lab as lab
import enrichment_probes as probes
import prepare_workload

PORTS = dict(
    CONNECT_PORT=8083,
    PROCESSOR_PORT=8080,
    POSTGRES_PORT=55432,
    KAFKA_BROKER_1_PORT=29092,
    KAFKA_BROKER_2_PORT=39092,
    KAFKA_BROKER_3_PORT=49092,
)


def inventory():
    # A successful complete listing, rather than a failed inspect, proves absence.
    ids = lab.call(["docker", "ps", "--all", "--quiet", "--no-trunc"]).splitlines()
    containers = json.loads(lab.call(["docker", "inspect", *ids])) if ids else []
    names = lab.call(["docker", "volume", "ls", "--quiet"]).splitlines()
    volumes = (
        json.loads(lab.call(["docker", "volume", "inspect", *names])) if names else []
    )
    ids = lab.call(["docker", "network", "ls", "--quiet"]).splitlines()
    networks = (
        json.loads(lab.call(["docker", "network", "inspect", *ids])) if ids else []
    )
    return {"containers": containers, "volumes": volumes, "networks": networks}


def owned(snapshot):
    rows = []
    for row in snapshot["containers"]:
        labels = row["Config"].get("Labels", {})
        if labels.get("com.docker.compose.project") == lab.PROJECT:
            if labels.get("com.docker.compose.project.working_dir") != str(lab.ROOT):
                raise RuntimeError("same Compose project belongs to another checkout")
            rows.append(row)
    return rows


def mounted_volumes(rows):
    return {m["Name"] for row in rows for m in row["Mounts"] if m["Type"] == "volume"}


def assert_absent(snapshot, names=()):
    if (
        owned(snapshot)
        or any(
            v["Name"] in names
            or v["Name"].startswith(lab.PROJECT + "_")
            or (v.get("Labels") or {}).get("com.docker.compose.project") == lab.PROJECT
            for v in snapshot["volumes"]
        )
        or any(
            (n.get("Labels") or {}).get("com.docker.compose.project") == lab.PROJECT
            for n in snapshot.get("networks", [])
        )
    ):
        raise RuntimeError("owned resources remain; refusing a fresh fixture")


def archive_closed(snapshot):
    receipt = lab.owner()
    old = lab.STATE / "resources.json"
    names = json.loads(old.read_text())["volumes"] if old.exists() else []
    assert_absent(snapshot, names)
    destination = lab.STATE / "archives" / receipt["owner"]
    destination.mkdir(parents=True, exist_ok=False)
    entries = [
        p for p in lab.STATE.iterdir() if p.name not in {"archives", "experiment.lock"}
    ]
    for path in entries:
        path.rename(destination / path.name)
    lab.save(
        destination / "archived.json",
        {
            "time": time.time(),
            "owner": receipt,
            "entries": sorted(p.name for p in entries),
        },
    )


def initialize():
    snapshot = inventory()
    if (lab.STATE / "CLOSED.json").exists():
        archive_closed(snapshot)
    path = lab.STATE / "owner.json"
    if path.exists():
        receipt = lab.owner()
        if receipt.get("root") != str(lab.ROOT):
            raise RuntimeError("ownership receipt does not match this checkout")
        return
    assert_absent(snapshot)
    if any(p.name not in {"archives", "experiment.lock"} for p in lab.STATE.iterdir()):
        raise RuntimeError("unowned local evidence requires diagnosis before setup")
    ports = {
        key: str(int(os.environ.get(key, default))) for key, default in PORTS.items()
    }
    if len(set(ports.values())) != len(ports) or any(
        not 1 <= int(v) <= 65535 for v in ports.values()
    ):
        raise ValueError("host ports must be distinct and between 1 and 65535")
    if int(os.environ.get("CUSTOMER_COUNT", "10000")) != 10000:
        raise ValueError("recovery workload requires the declared 10000 customers")
    lab.save(lab.STATE / "ports.json", ports)
    lab.save(
        path,
        {
            "project": lab.PROJECT,
            "owner": uuid.uuid4().hex,
            "root": str(lab.ROOT),
            "created_at": time.time(),
            "initial_containers": [r["Id"] for r in snapshot["containers"]],
            "initial_volumes": [v["Name"] for v in snapshot["volumes"]],
        },
    )
    lab.save(lab.STATE / "initial-runtime.json", snapshot)


def remember_resources():
    snapshot = inventory()
    rows = owned(snapshot)
    path = lab.STATE / "resources.json"
    previous = json.loads(path.read_text()) if path.exists() else {"volumes": []}
    names = mounted_volumes(rows) | set(previous["volumes"])
    if names & set(lab.owner()["initial_volumes"]):
        raise RuntimeError("owned service mounted a pre-existing foreign volume")
    lab.save(
        path,
        {
            "owner": lab.owner()["owner"],
            "volumes": sorted(names),
            "containers": [r["Id"] for r in rows],
        },
        replace=True,
    )
    return snapshot


def build_workloads(*, force=False):
    prepare_workload.main()
    materialized = lab.ROOT / ".build/enrichment"
    files = [p for p in materialized.rglob("*") if p.is_file()]
    tracked = lab.call(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            "processor",
            "proto",
            "enrichment-observer/Dockerfile",
        ]
    ).split("\0")
    files.extend(lab.ROOT / name for name in tracked if name)
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(lab.ROOT)).encode() + b"\0")
        digest.update(path.read_bytes())
    source_hash = digest.hexdigest()
    path = lab.STATE / "build.json"
    receipt = json.loads(path.read_text()) if path.exists() else {}
    images = lab.call(
        [
            "docker",
            "image",
            "ls",
            "--no-trunc",
            "--format",
            "{{.Repository}}:{{.Tag}} {{.ID}}",
        ]
    ).splitlines()
    available = dict(line.split() for line in images)
    if (
        not force
        and receipt.get("source_sha256") == source_hash
        and receipt.get("images")
        and all(
            available.get(tag) == identity
            for tag, identity in receipt["images"].items()
        )
    ):
        print(
            "Verified retained workload source and image IDs; no rebuild.", flush=True
        )
        return
    print(
        lab.compose(
            "--profile",
            "workloads",
            "build",
            "processor",
            "witness-producer",
            "source-reader",
            timeout=1800,
        ),
        flush=True,
    )
    tags = (
        lab.PROJECT + "-processor:latest",
        "kafka-enrichment-witness-producer:dev",
        "kafka-enrichment-observer:dev",
    )
    built = {
        tag: json.loads(lab.call(["docker", "image", "inspect", tag]))[0]["Id"]
        for tag in tags
    }
    lab.save(path, {"source_sha256": source_hash, "images": built}, replace=True)


def start_infrastructure():
    initialize()
    print(
        lab.compose(
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "300",
            *lab.BASE[:-1],
            timeout=600,
        ),
        flush=True,
    )
    remember_resources()


def topics():
    for name, policy in (
        ("orders", "delete"),
        ("orders.enriched", "delete"),
        ("pg.public.customers", "compact"),
    ):
        lab.cli(
            "kafka-topics.sh",
            "--create",
            "--if-not-exists",
            "--topic",
            name,
            "--partitions",
            "6",
            "--replication-factor",
            "3",
            "--config",
            "cleanup.policy=" + policy,
            "--config",
            "min.insync.replicas=2",
        )


def connector():
    port = lab.environment()["CONNECT_PORT"]
    config = (lab.ROOT / "connectors/postgres-customers.json").read_bytes()
    url = f"http://127.0.0.1:{port}/connectors/postgres-customers/config"
    request = urllib.request.Request(
        url, data=config, method="PUT", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        json.load(response)

    def running():
        status = lab.connect_status()
        return (
            status
            if status["connector"]["state"] == "RUNNING"
            and status["tasks"]
            and all(task["state"] == "RUNNING" for task in status["tasks"])
            else None
        )

    lab.wait(running, "Debezium connector and task RUNNING", 180)


def processor_environment():
    ids = lab.compose("ps", "--all", "--quiet", "processor").splitlines()
    if not ids:
        path = lab.STATE / "processor.json"
        return json.loads(path.read_text()) if path.exists() else {}
    if len(ids) != 1:
        raise RuntimeError("ambiguous processor identity")
    env = dict(s.split("=", 1) for s in lab.inspect(ids[0])["Config"]["Env"])
    return {
        name: env[key]
        for name, key in (
            ("INPUT_TOPIC", "INPUT_TOPIC"),
            ("OUTPUT_TOPIC", "OUTPUT_TOPIC"),
            ("PROCESSOR_GROUP", "CONSUMER_GROUP"),
            ("TRANSACTIONAL_ID", "TRANSACTIONAL_ID"),
        )
    }


def configure():
    start_infrastructure()
    assert_no_live_workloads(owned(inventory()))
    build_workloads()
    lab.sql((lab.ROOT / "postgres/probe.sql").read_text())
    topics()
    connector()
    lab.compose(
        "up",
        "--no-build",
        "--detach",
        "--wait",
        "processor",
        extra=processor_environment(),
        timeout=180,
    )
    lab.save(lab.STATE / "processor.json", processor_environment(), replace=True)
    remember_resources()
    result = lab.wait(control.native, "native RF3/ISR3, CDC and lookup readiness", 300)
    lab.save(lab.STATE / ("ready-" + str(time.time_ns()) + ".json"), result)
    if (lab.STATE / "cases").exists():
        lab.save(
            lab.STATE / ("reapply-verification-" + str(time.time_ns()) + ".json"),
            verify_history(),
        )


def verify_history():
    proofs = {}
    verifier = object.__new__(control.Experiment)
    for path in sorted((lab.STATE / "cases").glob("*/experiment.json")):
        root = path.parent
        metadata = json.loads(path.read_text())
        if metadata["owner"]["owner"] != lab.owner()["owner"]:
            raise RuntimeError("history belongs to a different fixture")
        if control.setup_only_failure(root):
            proofs[root.name] = {"setup_failure": True}
            continue
        if (
            (root / "failure.json").exists() or not (root / "result.json").exists()
        ) and not control.reconciled_failure(root):
            raise RuntimeError("unqualified history blocks teardown: " + root.name)
        proofs[root.name] = {
            side: verifier.verify(root.name, side) for side in ("source", "enriched")
        }
    control.check_negative()
    sql = probes.reconcile(lab.STATE, []) if (lab.STATE / "cases").exists() else {}
    return {"proofs": proofs, "postgres": sql}


def stop_process(rows, destination):
    running = [r["Id"] for r in rows if r["State"]["Running"]]
    if running:
        lab.call(["docker", "stop", "--time", "60", *running], timeout=180)
    stopped = [lab.inspect(r["Id"]) for r in rows]
    lab.save(destination, stopped)
    for row in stopped:
        # JVM shutdown hooks normally retain SIGTERM's 128+15 exit status.
        # Require native completion from this incarnation, never an older stop.
        log = subprocess.check_output(
            ["docker", "logs", "--since", row["State"]["StartedAt"], row["Id"]],
            cwd=lab.ROOT,
            env=lab.environment(),
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        (
            destination.parent / (destination.stem + "-" + row["Id"][:12] + ".log")
        ).write_text(log)
        if not clean_shutdown(row, log):
            raise RuntimeError(
                "unclean shutdown; stopped inspection and logs preserved"
            )


def clean_shutdown(row, log):
    state = row["State"]
    if state["Running"] or state["OOMKilled"]:
        return False
    role = row["Config"]["Labels"]["com.docker.compose.service"]
    marker = "Kafka Connect stopped" if role.startswith("connect-") else None
    if role.startswith("kafka-broker-"):
        marker = (
            "[BrokerServer id="
            + str(int(role.rsplit("-", 1)[1]) + 3)
            + "] shut down completed"
        )
    if role.startswith("kafka-controller-"):
        # KafkaRaftServer unregisters app-info only after controller.shutdown()
        # returns; ControllerServer has no separate completion log in 4.3.2.
        marker = "App info kafka.server for " + role.rsplit("-", 1)[1] + " unregistered"
    if marker:
        return state["ExitCode"] in {0, 143} and marker in log
    return state["ExitCode"] == 0


def stop_application():
    lab.owner()
    rows = owned(inventory())
    assert_no_live_workloads(rows)
    active = [
        r
        for r in rows
        if r["Config"]["Labels"]["com.docker.compose.service"] == "processor"
    ]
    if not active:
        return
    lab.save(lab.STATE / "processor.json", processor_environment(), replace=True)
    report = verify_history()
    lab.save(lab.STATE / ("down-verification-" + str(time.time_ns()) + ".json"), report)
    stop_process(
        active, lab.STATE / ("processor-stopped-" + str(time.time_ns()) + ".json")
    )
    lab.compose("rm", "--force", "processor")


def remove_unreferenced(names):
    snapshot = inventory()
    if mounted_volumes(snapshot["containers"]) & set(names):
        raise RuntimeError("a container still references a volume; refusing deletion")
    existing = {v["Name"] for v in snapshot["volumes"]}
    for name in sorted(set(names) & existing):
        lab.call(["docker", "volume", "rm", name])


def remove_workload_images():
    references = (
        "kafka-enrichment-witness-producer:dev",
        "kafka-enrichment-observer:dev",
    )
    # These two explicit tags survive Compose's --rmi local option. The build
    # label and a positive all-container listing establish ownership and non-use.
    available = set(
        lab.call(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"]
        ).splitlines()
    )
    removed = []
    for reference in references:
        if reference not in available:
            continue
        (image,) = json.loads(lab.call(["docker", "image", "inspect", reference]))
        if (image["Config"].get("Labels") or {}).get(
            "lab.example.system"
        ) != lab.PROJECT:
            raise RuntimeError("workload image lacks the owned build label")
        if any(r["Image"] == image["Id"] for r in inventory()["containers"]):
            raise RuntimeError("workload image is still used by a container")
        lab.call(["docker", "image", "rm", reference])
        removed.append({"reference": reference, "id": image["Id"]})
    return removed


def assert_no_live_workloads(rows):
    if any(
        r["State"]["Running"]
        and r["Config"]["Labels"]["com.docker.compose.service"] not in lab.BASE
        for r in rows
    ):
        raise RuntimeError("a workload is still running; diagnose before teardown")


def teardown():
    lab.owner()
    if (lab.STATE / "CLOSED.json").exists():
        assert_absent(inventory())
        return
    before = remember_resources()
    assert_no_live_workloads(owned(before))
    report = verify_history()
    stamp = str(time.time_ns())
    lab.save(lab.STATE / ("teardown-verification-" + stamp + ".json"), report)
    rows = owned(before)
    for group in (
        ("processor",),
        ("connect-1", "connect-2"),
        tuple(f"kafka-broker-{i}" for i in range(1, 4)),
        ("postgres",),
        tuple(f"kafka-controller-{i}" for i in range(1, 4)),
    ):
        selected = [
            r
            for r in rows
            if r["Config"]["Labels"]["com.docker.compose.service"] in group
        ]
        stop_process(
            selected, lab.STATE / ("stopped-" + stamp + "-" + group[0] + ".json")
        )
    lab.compose(
        "--profile",
        "apps",
        "--profile",
        "workloads",
        "down",
        "--volumes",
        "--remove-orphans",
        "--rmi",
        "local",
        timeout=300,
    )
    resources = json.loads((lab.STATE / "resources.json").read_text())
    remove_unreferenced(resources["volumes"])
    removed_images = remove_workload_images()
    after = inventory()
    assert_absent(after, resources["volumes"])
    foreign_before = {
        r["Id"]: r["State"] for r in before["containers"] if r not in rows
    }
    foreign_after = {r["Id"]: r["State"] for r in after["containers"]}
    if any(foreign_after.get(k) != v for k, v in foreign_before.items()):
        raise RuntimeError("foreign container identity/state changed during teardown")
    foreign_volumes = {
        v["Name"]: v for v in before["volumes"] if v["Name"] not in resources["volumes"]
    }
    remaining = {v["Name"]: v for v in after["volumes"]}
    if any(remaining.get(k) != v for k, v in foreign_volumes.items()):
        raise RuntimeError("foreign volume metadata changed during teardown")
    lab.save(
        lab.STATE / "CLOSED.json",
        {
            "time": time.time(),
            "owner": lab.owner(),
            "deleted_volumes": resources["volumes"],
            "removed_workload_images": removed_images,
            "foreign_containers": len(foreign_before),
            "foreign_volumes": len(foreign_volumes),
            "histories_preserved": True,
        },
    )


def main():
    lab.STATE.mkdir(exist_ok=True)
    with (lab.STATE / "experiment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        action = sys.argv[1]
        if action == "docker-up":
            start_infrastructure()
        elif action == "up":
            configure()
        elif action == "docker-down":
            teardown()
        elif action == "down":
            stop_application()
        else:
            lab.owner()
            if action == "kafka-topics":
                topics()
            elif action == "connector":
                connector()
            elif action == "probe-schema":
                lab.sql((lab.ROOT / "postgres/probe.sql").read_text())
            elif action == "workload-images":
                build_workloads(force=True)
            elif action == "verify-history":
                lab.save(
                    lab.STATE / ("history-" + str(time.time_ns()) + ".json"),
                    verify_history(),
                )
            elif action == "status":
                print(lab.compose("ps"))
                print(json.dumps(control.native(), indent=2))
            elif action == "logs":
                print(lab.compose("logs", "--tail=100", *lab.BASE))
            elif action == "cli":
                subprocess.run(
                    lab.COMPOSE
                    + ["exec", "postgres", "psql", "-U", "admin", "-d", "enrichment"],
                    cwd=lab.ROOT,
                    env=lab.environment(),
                    check=True,
                    timeout=3600,
                )
            else:
                raise ValueError("unknown lifecycle action")


if __name__ == "__main__":
    main()
