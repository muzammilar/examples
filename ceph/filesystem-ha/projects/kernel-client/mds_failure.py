"""One active-MDS crash while native kernel writes and historical reads stay live."""

import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

import control

sys.path.insert(0, str(control.ROOT / "scripts"))
import lab
import test as checks
from failover import roles


def actor(action, role, case, *args):
    return lab.command(
        "run",
        "--rm",
        "control",
        "/opt/lab/kernel-client/kernel_actors.py",
        action,
        role,
        case,
        *map(str, args),
        timeout=90
    ).stdout


def completed_rows(text):
    return [
        json.loads(line)
        for line in text.splitlines(keepends=True)
        if line.endswith("\n")
    ]


def main():
    baseline = json.loads(
        (control.ROOT / "results/kernel-client/fixture.json").read_text()
    )
    parent = checks.fixture()
    if baseline["stage"] != "ready" or parent["stage"] != "ready":
        raise RuntimeError("Require both verified baselines")
    if not 1 <= baseline["payload_bytes"] <= 524288:
        raise ValueError("The 512-file MDS case requires payloads at most 512 KiB")
    case = uuid.uuid4().hex
    path = (
        control.ROOT / "results/kernel-client" / ("mds-failure-" + str(time.time_ns()))
    )
    path.mkdir()
    control.save(path / "status.json", {"passed": False})
    control.save(
        path / "provenance.json",
        {
            str(source.relative_to(control.ROOT)): hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
            for source in [
                Path(__file__),
                Path(__file__).parent / "kernel_actors.py",
                Path(__file__).parent / "control.py",
                Path(__file__).parent / "contract.json",
                control.ROOT / "native/mds_fault.py",
                control.ROOT / "native/resources.py",
                Path(__file__).parent / "posix-workload-source/main.cpp",
            ]
        },
    )
    baseline_path = path / "baseline"
    baseline_path.mkdir()
    control.audit(baseline_path)
    previous_trials = list(control.trial_fixtures())
    for previous_path, previous in previous_trials:
        destination = baseline_path / previous_path.parent.name
        destination.mkdir()
        control.audit(destination, previous)
    exported = json.loads(control.command("export", 1, baseline["run"]).stdout)
    witness = Path(exported["path"]).relative_to("/results")
    trial_path = control.ROOT / "results/kernel-client/trials" / case
    trial_path.mkdir(parents=True)
    trial = {
        **baseline,
        "run": case,
        "files": 512,
        "stage": "initializing",
        "scenario": "live active-MDS loss",
    }
    control.save(trial_path / "fixture.json", trial)
    started_roles = []
    victim = None
    try:
        started_roles.append("writer")
        actor("start", "writer", case, case, 512, baseline["payload_bytes"])
        started_roles.append("reader")
        actor("start", "reader", case, baseline["run"], witness)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            writer = completed_rows(actor("logs", "writer", case))
            reader = completed_rows(actor("logs", "reader", case))
            if sum(row.get("event") == "ack" for row in writer) >= 5 and any(
                row.get("event") == "verified" for row in reader
            ):
                break
            time.sleep(1)
        else:
            raise TimeoutError("No live kernel writer/reader baseline progress")
        # Preserve the external witness outside the guests before the fault.
        control.command("export", 1, case)
        before, active = roles()
        if len(active) != 1 or not before["standbys"]:
            raise RuntimeError("Require one active MDS and a native standby")
        victim, gid = active[0]["name"], active[0]["gid"]
        control.save(path / "before-fsmap.json", before)
        for role in started_roles:
            state = json.loads(actor("status", role, case))
            if int(state["MainPID"]) <= 1 or state["SubState"] != "running":
                raise RuntimeError("Kernel actor not live at injection: " + str(state))
        started = time.monotonic()
        killed = json.loads(lab.native("mds_fault.py", "kill", victim).stdout)
        control.save(path / "fault.json", killed)
        end = time.monotonic() + 90
        with (path / "fsmap.jsonl").open("x") as history:
            while time.monotonic() < end:
                observed, active = roles()
                history.write(
                    json.dumps(
                        {
                            "elapsed_s": time.monotonic() - started,
                            "epoch": observed["epoch"],
                            "active": active,
                        }
                    )
                    + "\n"
                )
                history.flush()
                if (
                    len(active) == 1
                    and active[0]["name"] != victim
                    and active[0]["gid"] != gid
                ):
                    break
                time.sleep(1)
            else:
                raise TimeoutError("No native MDS promotion within 90 seconds")
        promoted_s = time.monotonic() - started
        control.save(path / "promoted-fsmap.json", observed)
        lab.native("mds_fault.py", "restore", victim)
        # Let the bounded writer finish; strict full-corpus ACK checks remain active.
        end = time.monotonic() + 150
        while time.monotonic() < end:
            status = json.loads(actor("status", "writer", case))
            if status["SubState"] == "exited" and status["ExecMainStatus"] == "0":
                break
            if status["ActiveState"] == "failed":
                raise RuntimeError("Kernel writer failed: " + str(status))
            time.sleep(2)
        else:
            raise TimeoutError("Kernel writer did not complete its bounded corpus")
        actor("stop", "reader", case)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            status = json.loads(actor("status", "reader", case))
            if status["SubState"] == "exited" and status["ExecMainStatus"] == "0":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Historical kernel reader did not stop cleanly")
        for role in started_roles:
            (path / (role + ".log")).write_text(actor("logs", role, case))
            control.save(
                path / (role + "-resources.json"),
                json.loads(actor("status", role, case)),
            )
        live_path = path / "live-readback"
        live_path.mkdir()
        control.audit(live_path, trial)
        old_data_path = path / "old-kernel-readback"
        old_data_path.mkdir()
        control.audit(old_data_path)
        for previous_path, previous in previous_trials:
            destination = old_data_path / previous_path.parent.name
            destination.mkdir()
            control.audit(destination, previous)
        # Do not qualify a recovered writer if the independent historical reader corrupted data.
        reader = checks.events((path / "reader.log").read_text())
        if any(row.get("event") in ("read_cycle_fail", "fatal") for row in reader):
            raise RuntimeError("Historical reader had errors; preserve failed result")
        library_path = path / "library-readback"
        library_path.mkdir()
        checks.audit(library_path)
        after, active = roles()
        if len(active) != 1 or not after["standbys"]:
            raise RuntimeError("MDS active/standby redundancy not restored")
        control.save(path / "after-fsmap.json", after)
        trial["stage"] = "ready"
        control.save(trial_path / "fixture.json", trial)
        control.save(
            path / "recovery.json",
            {
                "case": case,
                "native_promotion_s": promoted_s,
                "source_paused": False,
                "input_reloaded": False,
                "expected_acks": 512,
                "error_budget": 0,
            },
        )
        control.save(path / "status.json", {"passed": True})
        print(json.dumps({"passed": True, "evidence": str(path)}))
    finally:
        errors = []
        if victim:
            try:
                lab.native("mds_fault.py", "restore", victim)
            except Exception as error:
                errors.append("restore MDS: " + str(error))
        for role in started_roles:
            try:
                actor("stop", role, case)
                log = path / (role + ".log")
                if not log.exists():
                    log.write_text(actor("logs", role, case))
            except Exception as error:
                errors.append("stop/export " + role + ": " + str(error))
        control.save(path / "finalizer.json", {"errors": errors})
        if errors and sys.exc_info()[0] is None:
            control.save(path / "status.json", {"passed": False, "errors": errors})
            raise RuntimeError("Kernel recovery finalizer failed")


if __name__ == "__main__":
    main()
