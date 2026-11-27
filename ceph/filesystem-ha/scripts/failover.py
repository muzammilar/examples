"""Live-client MDS failover with preserved external ACKs and fresh readback."""

import json
import time
import uuid

import lab
import test as checks


def actor(action, role, run, *args):
    return lab.native("actors.py", action, role, run, *map(str, args), timeout=90)


def roles():
    observed = json.loads(checks.ssh(0, "ceph fs dump -f json").stdout)
    fs = next(f for f in observed["filesystems"] if f["mdsmap"]["fs_name"] == "labfs")
    active = [m for m in fs["mdsmap"]["info"].values() if m["state"] == "up:active"]
    if len(active) > 1:
        raise RuntimeError("Unexpected active MDS count")
    return observed, active


def progress(writer, reader, phase=None, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        values = []
        for role, run in (("writer", writer), ("reader", reader)):
            status = json.loads(actor("status", role, run).stdout)
            if int(status["MainPID"]) <= 1 or status["SubState"] != "running":
                raise RuntimeError("Workload actor exited early: " + str(status))
            rows = checks.events(actor("logs", role, run).stdout)
            predicate = (
                (lambda r: r.get("phase_start") == phase) if phase else (lambda r: True)
            )
            values.append(
                sum(
                    predicate(r)
                    and (
                        r.get("event") == "ack"
                        if role == "writer"
                        else r.get("result") == "ok"
                    )
                    for r in rows
                )
            )
        if values[0] >= 5 and values[1] >= 3:
            return values
        time.sleep(0.5)
    raise TimeoutError("No independent read/write progress in " + str(phase))


def finish_actor(role, run, directory):
    actor("stop", role, run)
    end = time.monotonic() + 35
    while time.monotonic() < end:
        status = json.loads(actor("status", role, run).stdout)
        if status["SubState"] == "exited" and status["ExecMainStatus"] == "0":
            (directory / (role + ".log")).write_text(actor("logs", role, run).stdout)
            checks.save(directory / (role + "-resources.json"), status)
            return
        if status["ActiveState"] == "failed":
            raise RuntimeError("Actor failed: " + str(status))
        time.sleep(0.5)
    raise TimeoutError("Actor did not stop cooperatively")


def main():
    fixture = checks.fixture()
    if fixture["stage"] != "ready":
        raise RuntimeError("Baseline not qualified")
    directory = lab.ROOT / "results" / ("mds-failover-" + str(time.time_ns()))
    directory.mkdir()
    checks.audit(directory)
    run = uuid.uuid4().hex
    seed = fixture["run"]
    case = lab.ROOT / "results/cases" / run
    case.mkdir()
    checks.save(
        case / "case.json",
        {"fixture": fixture["id"], "run": run, "node": 1, "mode": "mds-failover"},
    )
    old = None
    started_roles = []
    restored = False
    success = False
    try:
        for role, key in (("writer", run), ("reader", run)):
            actor(
                "start",
                role,
                key,
                fixture["files"],
                fixture["payload_bytes"],
                seed if role == "reader" else run,
            )
            started_roles.append((role, key))
        progress(run, run)
        for role, key in started_roles:
            actor("phase", role, key, "baseline")
        # Capture the durable witness outside the guest disks before injection.
        checks.export(1, run)
        before, active = roles()
        if len(active) != 1 or not before["standbys"]:
            raise RuntimeError("Active/standby topology is not ready")
        old = active[0]["name"]
        old_gid = active[0]["gid"]
        checks.save(directory / "before.json", before)
        for role, key in started_roles:
            actor("phase", role, key, "injection")
        started = time.monotonic()
        killed = json.loads(lab.native("mds_fault.py", "kill", old).stdout)
        checks.save(directory / "killed.json", killed)
        end = time.monotonic() + 90
        new = None
        with (directory / "fsmap.jsonl").open("x") as stream:
            while time.monotonic() < end:
                observed, active = roles()
                stream.write(
                    json.dumps(
                        {
                            "elapsed_s": time.monotonic() - started,
                            "active": active,
                            "epoch": observed["epoch"],
                        }
                    )
                    + "\n"
                )
                stream.flush()
                if (
                    len(active) == 1
                    and active[0]["name"] != old
                    and active[0]["gid"] != old_gid
                ):
                    new = active[0]
                    break
                time.sleep(1)
        if new is None:
            raise TimeoutError("No different active MDS within 90 seconds")
        failover_s = time.monotonic() - started
        lab.native("mds_fault.py", "restore", old)
        restored = True
        end = time.monotonic() + 90
        while time.monotonic() < end:
            observed, active = roles()
            if any(m["name"] == old for m in observed["standbys"]):
                break
            time.sleep(1)
        else:
            raise TimeoutError("Old MDS did not restore standby redundancy")
        for role, key in started_roles:
            actor("phase", role, key, "after")
        progress(run, run, phase="after")
        finish_actor("writer", run, directory)
        # Reader remains live while fresh clients reconcile every acknowledged body.
        result = checks.audit(directory)
        finish_actor("reader", run, directory)
        checks.save(
            directory / "result.json",
            result
            | {
                "mds_failover": "passed",
                "old": old,
                "new": new["name"],
                "observed_failover_s": failover_s,
                "restored_standby": True,
                "input_reloaded": False,
            },
        )
        success = True
    finally:
        if old and not restored:
            lab.native("mds_fault.py", "restore", old)
        for role, key in started_roles:
            status = json.loads(actor("status", role, key).stdout)
            if int(status["MainPID"]) > 1:
                try:
                    finish_actor(role, key, directory)
                except (RuntimeError, TimeoutError):
                    checks.ssh(
                        1 if role == "writer" else 2,
                        "systemctl stop cephfs-" + role + "-" + key + ".service",
                    )
            (directory / (role + "-final.log")).write_text(
                actor("logs", role, key).stdout
            )
        checks.save(
            directory / "status.json", {"passed": success, "witness_preserved": True}
        )
    print(json.dumps({"passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
