"""Stage one incomplete HTTP body against an owned SeaweedFS S3 gateway."""

from contextlib import ExitStack
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import time
import uuid

import control as c
from witness import attempted_objects, records


def wait_file(path, actors, seconds=90):
    deadline = time.monotonic() + seconds
    while not path.exists():
        c.live(actors)
        if time.monotonic() >= deadline:
            raise TimeoutError("Missing application barrier: " + path.name)
        time.sleep(0.1)
    return json.loads(path.read_text())


def main():
    before = c.fixture_check()
    c.history()
    c.verify(c.BASE)
    case = c.BASE / ("fault-multipart-" + uuid.uuid4().hex)
    case.mkdir()
    c.save(
        case / "case.json",
        {
            "id": uuid.uuid4().hex,
            "mode": "multipart",
            "target": "s3api0",
            "controller_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        },
    )
    c.save(case / "before.json", before)
    for name in (
        "manifest",
        "cases-manifest",
        "benchmark-manifest",
        "benchmark-attempts",
        "fault-history",
        "fixture",
    ):
        path = c.BASE / (name + ".json")
        if path.exists():
            shutil.copy2(path, case / path.name)
    actors = {}
    names = {}
    success = False
    injected = False
    with ExitStack() as stack:
        try:
            for role in ("writer", "reader", "multipart"):
                names[role] = c.PROJECT + "-" + role + "-" + uuid.uuid4().hex[:12]
                log = stack.enter_context((case / (role + ".log")).open("w"))
                arguments = (
                    ["/app/multipart_workload.py"]
                    if role == "multipart"
                    else ["/app/traffic.py", role]
                )
                actors[role] = subprocess.Popen(
                    [
                        *c.COMPOSE,
                        "run",
                        "--rm",
                        "-T",
                        "--name",
                        names[role],
                        "--user",
                        c.USER,
                        "--entrypoint",
                        "python",
                        ("multipart-workload" if role == "multipart" else "tools"),
                        *arguments,
                    ],
                    env=c.env_for(case),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            regular = {role: actors[role] for role in ("writer", "reader")}
            c.phase(case, "baseline")
            c.progress(case, regular, 0)
            held = wait_file(case / "request-held.json", actors)
            expected_source = hashlib.sha256(
                (c.ROOT / "multipart-workload/main.py").read_bytes()
            ).hexdigest()
            if (
                json.loads((case / "multipart-source.json").read_text())["sha256"]
                != expected_source
            ):
                raise RuntimeError(
                    "Application source differs from built image; refusing fault"
                )
            addresses = {
                net["IPAddress"]
                for net in before["s3api0"]["NetworkSettings"]["Networks"].values()
            }
            if (
                held["peer"][0] not in addresses
                or held["peer"][1] != 8333
                or not 0 < held["bytes_sent"] < held["content_length"]
            ):
                raise RuntimeError(
                    "Held request does not identify an incomplete body at the owned endpoint"
                )
            c.phase(case, "injection")
            c.compose("kill", "-s", "SIGKILL", "s3api0")
            injected = True
            stopped = json.loads(c.run(["docker", "inspect", before["s3api0"]["Id"]]))[
                0
            ]
            if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 137:
                raise RuntimeError("Target did not die with exit 137")
            c.save(case / "stopped.json", stopped)
            c.phase(case, "outage")
            time.sleep(3)
            c.save(case / "release-request", {})
            # Keep normal readers/writers live during reconnect and native recovery.
            c.phase(case, "repair")
            started = time.monotonic()
            c.compose(
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "120",
                "s3api0",
                timeout=180,
            )
            ready_seconds = time.monotonic() - started
            c.progress(case, regular, time.time())
            c.save(case / "server-recovered", {})
            if actors["multipart"].wait(timeout=150):
                raise RuntimeError(
                    "Multipart application failed; inspect multipart.log"
                )
            c.check_survivors(before, c.inventory(), "s3api0")
            old = (
                json.loads((case / "fault-history.json").read_text())
                if (case / "fault-history.json").exists()
                else []
            )
            c.save(
                case / "fault-history.json",
                old + attempted_objects(records(case / "multipart.jsonl")),
            )
            prefix = case / "live-repair-prefix"
            prefix.mkdir()
            for name in (
                "manifest",
                "cases-manifest",
                "benchmark-manifest",
                "benchmark-attempts",
                "fault-history",
                "fixture",
                "multipart-published",
            ):
                p = case / (name + ".json")
                if p.exists():
                    shutil.copy2(p, prefix / p.name)
            data = (case / "churn.jsonl").read_bytes()
            data = data[: data.rfind(b"\n") + 1]
            with (prefix / "churn.jsonl").open("xb") as output:
                output.write(data)
            c.native(prefix, "snapshot")
            c.repair(case)
            c.native(prefix, "wait")
            c.bytes_audit(prefix)
            c.live(regular)
            repaired = time.time()
            c.phase(case, "after")
            c.progress(case, regular, repaired)
            (case / "stop-churn").touch()
            if actors["writer"].wait(timeout=30):
                raise RuntimeError("Live writer failed")
            c.phase(case, "reconcile")
            c.repair(case)
            c.verify(case)
            c.live({"reader": actors["reader"]})
            c.phase(case, "verified")
            (case / "stop-reader").touch()
            if actors["reader"].wait(timeout=30):
                raise RuntimeError("Independent reader failed")
            c.check_survivors(before, c.inventory(), "s3api0")
            result = c.report(case) | {
                "mode": "multipart",
                "target": "s3api0",
                "live_prefix": {"bounded_ack_prefix": True},
                "ready_seconds": ready_seconds,
                "survivors_unchanged": len(c.SERVICES) - 1,
                "live_prefix_recovery": json.loads(
                    (prefix / "physical-recovery.json").read_text()
                ),
                "multipart": json.loads((case / "multipart-summary.json").read_text()),
            }
            c.save(case / "result.json", result)
            success = True
        finally:
            cleanup_errors = []
            for role, actor in actors.items():
                if actor.poll() is None:
                    if role in ("writer", "reader"):
                        (
                            case / ("stop-churn" if role == "writer" else "stop-reader")
                        ).touch()
                    try:
                        actor.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        try:
                            c.run(
                                ["docker", "stop", "--time", "5", names[role]],
                                timeout=20,
                            )
                        except (
                            subprocess.CalledProcessError,
                            subprocess.TimeoutExpired,
                        ) as error:
                            cleanup_errors.append(str(error))
                        actor.wait(timeout=20)
            if injected:
                try:
                    c.compose("up", "-d", "--no-deps", "--wait", "s3api0", timeout=180)
                except (
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                ) as error:
                    cleanup_errors.append(str(error))
            c.save(
                case / "status.json",
                {
                    "passed": success and not cleanup_errors,
                    "cleanup_errors": cleanup_errors,
                    "disk_removed": False,
                },
            )
            if cleanup_errors:
                raise RuntimeError(cleanup_errors)
    c.history()
    print(json.dumps({"passed": True, "case": str(case)}))


if __name__ == "__main__":
    main()
