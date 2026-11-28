"""Keep native clients and external ACK witnesses live through owned guest loss."""

import json
import os
import sys
import time
import uuid
import lab
import test as checks
import failover


def main():
    mode = sys.argv[1]
    if mode not in ("process", "disk"):
        raise ValueError(mode)
    fixture = checks.fixture()
    if fixture["stage"] != "ready":
        raise RuntimeError("Baseline not qualified")
    writer_bytes = int(os.environ.get("FAILURE_PAYLOAD_BYTES", "65536"))
    if not 1 <= writer_bytes <= 16777216 or writer_bytes * fixture["files"] > 268435456:
        raise ValueError("Fault-writer payload exceeds the declared actor bounds")
    directory = lab.ROOT / "results" / (mode + "-failure-" + str(time.time_ns()))
    directory.mkdir()
    checks.audit(directory)
    checks.save(
        directory / "resources-before.json",
        json.loads(lab.native("resources.py").stdout),
    )
    run = uuid.uuid4().hex
    case = lab.ROOT / "results/cases" / run
    case.mkdir()
    checks.save(
        case / "case.json",
        {"fixture": fixture["id"], "run": run, "node": 1, "mode": mode + "-failure"},
    )
    started = []
    injected = False
    restored = False
    passed = False
    try:
        for role in ("writer", "reader"):
            failover.actor(
                "start",
                role,
                run,
                fixture["files"],
                fixture["payload_bytes"] if role == "reader" else writer_bytes,
                fixture["run"] if role == "reader" else run,
                600,
            )
            started.append(role)
        before = failover.progress(run, run)
        checks.export(1, run)
        for role in started:
            failover.actor("phase", role, run, "injection")
        began = time.monotonic()
        injected = True
        fault = lab.native("storage_fault.py", "inject", mode, timeout=120)
        (directory / "injection.log").write_text(fault.stdout + fault.stderr)
        for name in ("injection", "guest-boots-before"):
            checks.save(
                directory / (name + ".json"),
                json.loads((lab.ROOT / "results" / (name + ".json")).read_text()),
            )
        # Observe actual native failure, not just completion of the fault tool.
        end = time.monotonic() + 120
        observed = []
        while time.monotonic() < end:
            value = json.loads(checks.ssh(1, "ceph status -f json").stdout)
            observed.append({"elapsed_s": time.monotonic() - began, "status": value})
            up = value["osdmap"]["num_up_osds"]
            if up <= (6 if mode == "process" else 8):
                break
            time.sleep(2)
        else:
            raise TimeoutError("Native OSD membership did not observe the held fault")
        checks.save(directory / "degraded.json", observed)
        # Start a fresh phase only after native OSD membership observed loss.
        for role in started:
            failover.actor("phase", role, run, "degraded")
        # Require *new* successful reads and ACKs while the fault is held.
        degraded = failover.progress(run, run, phase="degraded", timeout=90)
        checks.export(1, run)
        recovery_began = time.monotonic()
        recovery = lab.native("storage_fault.py", "recover", mode, timeout=1500)
        restored = True
        native_recovery_s = time.monotonic() - recovery_began
        (directory / "recovery.log").write_text(recovery.stdout + recovery.stderr)
        for role in started:
            failover.actor("phase", role, run, "after")
        # A bounded actor may finish during slow emulated repair. Require its final
        # successful exit, then verify every original ACK; never restart/reseed it.
        status = json.loads(failover.actor("status", "writer", run).stdout)
        after = None
        if int(status["MainPID"]) > 1:
            after = failover.progress(run, run, phase="after", timeout=45)
        for role in started:
            status = json.loads(failover.actor("status", role, run).stdout)
            if int(status["MainPID"]) > 1:
                failover.finish_actor(role, run, directory)
            elif status["ExecMainStatus"] != "0":
                raise RuntimeError("Actor failed during recovery: " + str(status))
            (directory / (role + ".log")).write_text(
                failover.actor("logs", role, run).stdout
            )
            checks.save(
                directory / (role + "-resources.json"),
                json.loads(failover.actor("status", role, run).stdout),
            )
        result = checks.audit(directory)
        if mode == "process":
            original = json.loads(
                (lab.ROOT / "results/guest-boots-before.json").read_text()
            )
            current = checks.ssh(
                0, "cat /proc/sys/kernel/random/boot_id"
            ).stdout.strip()
            if current == original["0"]:
                raise RuntimeError("No different guest boot identity")
            checks.save(
                directory / "reboot.json", {"before": original["0"], "after": current}
            )
        else:
            checks.save(
                directory / "replacement.json",
                json.loads((lab.ROOT / "results/replacement.json").read_text()),
            )
        checks.save(
            directory / "result.json",
            result
            | {
                "fault": mode,
                "writer_payload_bytes": writer_bytes,
                "historical_payload_bytes": fixture["payload_bytes"],
                "baseline_progress": before,
                "held_fault_progress": degraded,
                "after_progress": after,
                "native_recovery_s": native_recovery_s,
                "recovery_and_verification_s": time.monotonic() - recovery_began,
                "elapsed_s": time.monotonic() - began,
                "input_reloaded": False,
            },
        )
        checks.save(
            directory / "resources-after.json",
            json.loads(lab.native("resources.py").stdout),
        )
        passed = True
    finally:
        if injected and not restored:
            try:
                lab.native("storage_fault.py", "recover", mode, timeout=1500)
            except Exception as error:
                (directory / "recovery-failed.txt").write_text(str(error))
        for role in started:
            try:
                status = json.loads(failover.actor("status", role, run).stdout)
                if int(status["MainPID"]) > 1:
                    failover.finish_actor(role, run, directory)
                (directory / (role + "-final.log")).write_text(
                    failover.actor("logs", role, run).stdout
                )
            except Exception as error:
                (directory / (role + "-cleanup-failed.txt")).write_text(str(error))
        checks.save(
            directory / "status.json",
            {"passed": passed, "fault": mode, "witness_preserved": True},
        )
    print(json.dumps({"passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
