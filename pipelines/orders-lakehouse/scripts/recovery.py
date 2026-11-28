"""Separate publication and maintenance faults with uninterrupted source traffic."""

import json
import datetime
import sys
import time
import uuid
import lab
import pipeline as p


def ancestry(frozen, final):
    if frozen["table_uuid"] != final["table_uuid"]:
        raise ValueError("Table identity changed across recovery")
    chain = {row["id"]: row["parent"] for row in final["snapshots"]}
    current, seen = final["snapshot"], set()
    while current is not None and current not in seen:
        if current == frozen["snapshot"]:
            return
        seen.add(current)
        current = chain.get(current)
    raise ValueError("Final snapshot is not descended from the frozen baseline")


def cleanup(path, holder=None, killed=None, compact=None, writer=None, reader=None):
    errors = []
    actions = []
    if holder:
        actions.append(lambda: holder.finish(stop=True))
    if killed:
        actions.append(lambda: restore(path, *killed))
    if compact:
        actions.append(compact.finish)
    if writer:
        actions.append(writer.finish)
    if reader:
        actions.append(lambda: reader.finish(stop=True))
    for action in actions:
        try:
            action()
        except Exception as error:
            errors.append(str(error))
    if errors:
        lab.save(path / "cleanup-errors.json", errors)
        if sys.exc_info()[0] is None:
            raise RuntimeError(
                "Recovery cleanup failed; retained diagnostics: " + str(errors)
            )


def progress(actor):
    actor.alive()
    values = [
        r["acknowledged"] for r in lab.records(actor.logpath) if "acknowledged" in r
    ]
    return values[-1] if values else 0


def signal_role(path, role, capture=True):
    identifier = lab.compose("ps", "--quiet", role).strip()
    row = lab.owned(identifier, role)
    if not row["State"]["Running"]:
        raise ValueError("Fault target already down")
    lab.save(path / "fault-before.json", row)
    if capture:
        lab.snapshot(path, "pre-fault")
    lab.output("docker", "kill", "--signal", "KILL", identifier)
    failed = lab.owned(identifier, role)
    lab.save(path / "fault-killed.json", failed)
    if (
        failed["State"]["Running"]
        or failed["State"]["ExitCode"] != 137
        or failed["State"]["OOMKilled"]
    ):
        raise ValueError("SIGKILL not proven")
    return identifier, row["State"]["StartedAt"]


def restore(path, identifier, started):
    lab.owned(identifier)
    lab.output("docker", "start", identifier)
    row = lab.owned(identifier)
    lab.save(path / "fault-restored.json", row)
    if not row["State"]["Running"] or row["State"]["StartedAt"] == started:
        raise ValueError("Restart not proven")


def native_recovery(path, before_path=None):
    before_path = before_path or path
    before = json.loads((before_path / "pre-fault-inspect.json").read_text())
    target = json.loads((before_path / "fault-before.json").read_text())["Id"]
    changed = []
    for row in before:
        if (
            row["Config"]["Labels"]["com.docker.compose.service"] not in lab.ROLES
            or row["Id"] == target
        ):
            continue
        now = lab.owned(row["Id"])
        if (
            not now["State"]["Running"]
            or now["State"]["StartedAt"] != row["State"]["StartedAt"]
        ):
            changed.append(row["Name"])
    lab.save(path / "unexpected-survivor-restarts.json", changed)
    if changed:
        raise ValueError("Unrelated native roles restarted: " + str(changed))
    stamp = datetime.datetime.fromtimestamp(
        json.loads((before_path / "status.json").read_text())["started_ns"] / 1e9,
        datetime.timezone.utc,
    ).isoformat()
    (path / ("native-flink-recovery-" + str(time.time_ns()) + ".log")).write_text(
        lab.compose(
            "logs",
            "--no-color",
            "--since",
            stamp,
            "jobmanager",
            "taskmanager1",
            "taskmanager2",
            timeout=45,
        )
    )
    lab.save(path / "recovered-kafka.json", p.control(path, "audit-kafka"))


def begin(path, fixture, phase, prior):
    if fixture["phases"] != prior:
        raise ValueError(
            "Case is one-shot per preserved fixture; previous phase order differs"
        )
    frozen = p.settled(path, prior)
    reader = p.Background(path, "lake-oracle", ["watch", frozen["metadata"]])
    writer = None
    try:
        p.wait(
            lambda: any(r.get("read") == "ok" for r in lab.records(reader.logpath)), 90
        )
        writer = p.Background(path, "orders", [phase, "1", fixture["rows"]])
        fixture["phases"] += [phase]
        lab.save(lab.ROOT / "results/fixture.json", fixture)
        p.wait(lambda: progress(writer) >= 1000, 90)
    except BaseException:
        cleanup(path, writer=writer, reader=reader)
        raise
    return frozen, reader, writer


def publication(path, fixture):
    # Lock is held before the first new source record; no checkpoint publication can race setup.
    case = uuid.uuid4().hex
    holder = p.Background(path, "lake-control", ["lock", case])
    writer = reader = None
    killed = None
    try:
        p.wait(
            lambda: any(r.get("case") == case for r in lab.records(holder.logpath)), 60
        )
        lock_receipt = next(
            r for r in lab.records(holder.logpath) if r.get("case") == case
        )
        frozen, reader, writer = begin(path, fixture, "live", ["baseline"])
        blocked = p.wait(lambda: p.control(path, "blocked")["blocked"], 45)
        lab.save(path / "native-blocked.json", blocked)
        owner = blocked[0]["host"].split("/")[0]
        roles = {}
        for role in ("taskmanager1", "taskmanager2"):
            identifier = lab.compose("ps", "--quiet", role).strip()
            row = lab.owned(identifier, role)
            for network in row["NetworkSettings"]["Networks"].values():
                roles[network["IPAddress"]] = role
        if (
            owner not in roles
            or lock_receipt["pid"] not in blocked[0]["blockers"]
            or "UPDATE" not in blocked[0]["query"].upper()
        ):
            raise ValueError(
                "Blocked catalog publication is not owned by an active TaskManager"
            )
        start = time.time_ns()
        original_job = p.job()["jid"]
        checkpoint_before = p.rest("jobs/" + original_job + "/checkpoints")
        lab.save(path / "checkpoints-before.json", checkpoint_before)
        before = progress(writer)
        killed = signal_role(path, roles[owner])
        time.sleep(10)
        during = progress(writer)
        if during <= before:
            raise ValueError("Source ACK progress stopped during publication outage")
        reader.alive()
        holder.finish(stop=True)
        holder = None
        restore(path, *killed)
        killed = None
        writer.finish()
        writer = None
        p.wait(p.job, 180)
        if p.job()["jid"] != original_job:
            raise ValueError("Recovery substituted another Flink job")
        checkpoint_after = p.rest("jobs/" + original_job + "/checkpoints")
        lab.save(path / "checkpoints.json", checkpoint_after)
        if (
            checkpoint_after["counts"]["restored"]
            <= checkpoint_before["counts"]["restored"]
        ):
            raise ValueError("No native checkpoint restoration after the worker crash")
        final = p.verify(path, fixture["phases"])
        ancestry(frozen, final["snapshot"])
        old = p.verify(path, ["baseline"], frozen=frozen)
        reader.finish(stop=True)
        reader = None
        native_recovery(path)
        lab.save(
            path / "recovery.json",
            dict(
                passed=True,
                fault_at_ns=start,
                before_acks=before,
                outage_acks=during,
                final=final,
                frozen_read=old,
                reconciled_at_ns=time.time_ns(),
            ),
        )
    finally:
        cleanup(path, holder=holder, killed=killed, writer=writer, reader=reader)


def spark_api(actor, endpoint):
    actor.alive()
    return json.loads(
        lab.output(
            "docker",
            "exec",
            actor.name,
            "python3",
            "-c",
            "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:4040/api/v1/"
            + endpoint
            + "',timeout=3).read().decode())",
            timeout=10,
        )
    )


def compaction(path, fixture):
    frozen, reader, writer = begin(path, fixture, "maintenance", ["baseline", "live"])
    compact = None
    killed = None
    try:
        worker_roles = {}
        for candidate in ("spark-worker1", "spark-worker2"):
            row = lab.owned(lab.compose("ps", "--quiet", candidate).strip(), candidate)
            worker_roles[candidate] = candidate
            for network in row["NetworkSettings"]["Networks"].values():
                worker_roles[network["IPAddress"]] = candidate
        # Do the expensive all-role meter before task discovery, not between discovery and SIGKILL.
        lab.snapshot(path, "pre-fault")
        compact = p.Background(path, "spark-job", ["compact"])
        apps = p.wait(lambda: spark_api(compact, "applications"), 90)
        app = apps[0]["id"]
        stages = p.wait(
            lambda: spark_api(compact, "applications/" + app + "/stages?status=active"),
            90,
        )
        lab.save(path / "active-compaction-stages.json", stages)
        executors = spark_api(compact, "applications/" + app + "/executors")
        active = [e for e in executors if e["id"] != "driver" and e["activeTasks"] > 0]
        if not active:
            raise ValueError("No active native compaction executor at injection")
        host = active[0]["hostPort"].split(":")[0]
        role = worker_roles.get(host)
        if role is None:
            raise ValueError("Cannot map active executor to owned worker")
        lab.save(path / "active-executors.json", executors)
        before = progress(writer)
        killed = signal_role(path, role, capture=False)
        time.sleep(10)
        during = progress(writer)
        if during <= before:
            raise ValueError("Producer exhausted/stopped during maintenance recovery")
        reader.alive()
        restore(path, *killed)
        killed = None
        compact.finish()
        receipts = [r for r in lab.records(compact.logpath) if "compaction" in r]
        if (
            not receipts
            or not receipts[0]["compaction"]
            or receipts[0]["compaction"][0]["rewritten_data_files_count"] < 1
        ):
            raise ValueError("No native compaction replacement receipt")
        event_script = """import json,pathlib,sys
events=[]
for file in pathlib.Path('/data/events').rglob('*'):
 if file.is_file() and file.name.startswith(sys.argv[1]):
  for line in file.read_text().splitlines():
   row=json.loads(line)
   if row.get('Event')=='SparkListenerTaskEnd' and row['Task Info']['Failed']:
    events.append(row)
print(json.dumps(events))
"""
        failed_tasks = json.loads(
            lab.compose(
                "exec", "-T", "spark-worker1", "python3", "-c", event_script, app
            )
        )
        lab.save(path / "native-failed-tasks.json", failed_tasks)
        if not failed_tasks:
            raise ValueError("No native failed task proves compaction recomputation")
        compact = None
        writer.finish()
        writer = None
        final = p.verify(path, fixture["phases"])
        ancestry(frozen, final["snapshot"])
        old = p.verify(path, ["baseline", "live"], frozen=frozen)
        reader.finish(stop=True)
        reader = None
        native_recovery(path)
        lab.save(
            path / "recovery.json",
            dict(
                passed=True,
                before_acks=before,
                outage_acks=during,
                final=final,
                frozen_read=old,
                app_id=app,
            ),
        )
    finally:
        cleanup(path, killed=killed, compact=compact, writer=writer, reader=reader)
