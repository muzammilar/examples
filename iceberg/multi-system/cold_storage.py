"""Cold whole-volume checkpoint and separate restore; never removes source storage."""

import fcntl
import hashlib
import json
import os
import subprocess
import time
import uuid

import recovery_control as control
import recovery_cleanup as cleanup
from recovery_resources import ResourceSampler

LABEL = "org.examples.cold-storage"
IMAGE = "iceberg-multi-runner-duckdb2:dev"
BACKUP = r"""
set -eu
cd /source
if test -n "$(find . -xdev -type l -print -quit)"; then
  echo 'Unexpected symbolic link in owned storage; preserve and inspect' >&2; exit 1
fi
find . -xdev -printf '%y %m %U %G %p\n' | LC_ALL=C sort > /backup/metadata.txt
find . -xdev -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > /backup/files.sha256
du -sk . > /backup/source-allocated-kib.txt
date +%s%N > /backup/archive-started-ns.txt
tar --sparse --numeric-owner -czpf /backup/storage.tar.gz .
sync /backup/storage.tar.gz
sha256sum /backup/storage.tar.gz > /backup/archive.sha256
date +%s%N > /backup/archive-completed-ns.txt
"""
RESTORE = r"""
set -eu
sha256sum --check /backup/archive.sha256
cd /restore
test -z "$(find . -mindepth 1 -print -quit)"
tar --numeric-owner -xzpf /backup/storage.tar.gz
sha256sum --check /backup/files.sha256 > /backup/restore-hash-check.txt
find . -xdev -printf '%y %m %U %G %p\n' | LC_ALL=C sort > /backup/restored-metadata.txt
cmp /backup/metadata.txt /backup/restored-metadata.txt
find . -xdev -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > /backup/restored-files.sha256
cmp /backup/files.sha256 /backup/restored-files.sha256
du -sk . > /backup/restore-allocated-kib.txt
sync /restore
"""


def validate_volume(volume, name):
    if (
        volume["Name"] != name
        or volume.get("Labels", {}).get("com.docker.compose.project") != control.PROJECT
        or volume.get("Driver") != "local"
        or volume.get("Options")
    ):
        raise RuntimeError("source must be an exact local example-owned volume")


def stopped(value):
    state = value["State"]
    if (
        state["Running"]
        or state["Pid"] != 0
        or state["ExitCode"] != 0
        or state["OOMKilled"]
        or state.get("Error")
    ):
        raise RuntimeError("clean native shutdown was not proven; preserve diagnostics")


def inspect(identifier):
    (value,) = json.loads(control.call(["docker", "inspect", identifier]))
    return value


def volume_users(name):
    identifiers = control.call(
        ["docker", "ps", "--no-trunc", "--filter", "volume=" + name, "-q"]
    ).split()
    return set(identifiers)


def original_services():
    services = {r: control.service(r) for r in ("storage", "clickhouse")}
    running = set(
        control.call(
            [
                "docker",
                "ps",
                "--no-trunc",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + control.PROJECT,
            ]
        ).split()
    )
    if running != {v["Id"] for v in services.values()}:
        raise RuntimeError("stop all example workload actors before cold checkpoint")
    for role, value in services.items():
        if (
            not value["State"]["Running"]
            or value["State"].get("Health", {}).get("Status") != "healthy"
        ):
            raise RuntimeError("healthy original services required")
        signal = value["Config"].get("StopSignal") or "SIGTERM"
        if signal not in ("SIGTERM", "15"):
            raise RuntimeError("unexpected effective native stop signal")
        command = control.call(
            ["docker", "exec", value["Id"], "cat", "/proc/1/comm"]
        ).strip()
        expected = "weed" if role == "storage" else "clickhouse-serv"
        if command != expected:
            raise RuntimeError("unexpected actual PID 1 for " + role)
    return services


def capabilities(role):
    # The read-only source mount prevents writes despite DAC_OVERRIDE. Restore
    # additionally needs ownership/mode changes to reproduce private metadata.
    if role == "backup":
        return ["DAC_OVERRIDE"]
    if role == "restore":
        return ["DAC_OVERRIDE", "CHOWN", "FOWNER"]
    raise ValueError("unknown checkpoint helper role")


def finish_helper(identifier, directory, role, sampler):
    """Always stop collection; return cleanup errors without hiding the main failure."""
    errors = []
    try:
        value = inspect(identifier)
        if value["State"]["Running"]:
            control.call(["docker", "kill", identifier])
        value = inspect(identifier)
        control.save(directory / (role + "-final.json"), control.state(value))
        if value["State"]["Running"] or value["State"]["Pid"] != 0:
            raise RuntimeError("checkpoint helper fencing is unproven")
    except BaseException as error:
        errors.append(error)
    try:
        logs = subprocess.run(
            ["docker", "logs", identifier],
            env=control.DOCKER_ENV,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        control.save_text(directory / (role + "-console.log"), logs.stdout)
        if logs.returncode:
            raise RuntimeError("could not retain helper logs")
    except BaseException as error:
        errors.append(error)
    try:
        _, summary = sampler.close()
        control.save(directory / (role + "-resources.json"), summary)
    except BaseException as error:
        errors.append(error)
    return errors


def helper(directory, run_id, role, image, mounts, script):
    name = "iceberg-cold-" + run_id.hex + "-" + role
    gate = """set -euo pipefail
while ! test -f /backup/ROLE.release; do sleep 0.1; done
SCRIPT
cat /sys/fs/cgroup/cpu.stat > /backup/ROLE.cpu.stat
cat /sys/fs/cgroup/memory.peak > /backup/ROLE.memory.peak
cat /sys/fs/cgroup/io.stat > /backup/ROLE.io.stat
touch /backup/ROLE.completed
while ! test -f /backup/ROLE.finish; do sleep 0.1; done
""".replace("SCRIPT", script).replace("ROLE", role)
    identifier = control.call(
        [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            LABEL + "=" + str(run_id),
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--read-only",
            "--cap-drop",
            "ALL",
            *[item for cap in capabilities(role) for item in ("--cap-add", cap)],
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "0:0",
            "--mount",
            "type=bind,src=" + str(directory) + ",dst=/backup",
            *mounts,
            "--entrypoint",
            "/bin/bash",
            image,
            "-c",
            gate,
        ]
    ).strip()
    initial = inspect(identifier)
    control.save(directory / (role + "-created.json"), control.state(initial))
    if (
        initial["Config"]["Labels"].get(LABEL) != str(run_id)
        or initial["Name"] != "/" + name
        or initial["Image"] != image
        or initial["HostConfig"]["NetworkMode"] != "none"
        or initial["HostConfig"]["Memory"] != 512 * 1024**2
        or initial["HostConfig"]["NanoCpus"] != 1000000000
        or {cap.removeprefix("CAP_") for cap in initial["HostConfig"]["CapAdd"]}
        != set(capabilities(role))
    ):
        raise RuntimeError("helper identity or resource budget differs")
    sampler = ResourceSampler(directory / role, env=control.DOCKER_ENV)
    sampler.directory.mkdir()
    sampler.bind(role, identifier, "/source" if role == "backup" else "/restore")
    sampler.start()
    primary = None
    try:
        control.call(["docker", "start", identifier])
        deadline = time.monotonic() + 20
        while True:
            value = inspect(identifier)
            if not value["State"]["Running"]:
                raise RuntimeError("helper exited before release")
            try:
                sampler.require_coverage([role], 0)
                break
            except (FileNotFoundError, RuntimeError):
                if time.monotonic() > deadline:
                    raise TimeoutError("helper resource preflight timed out")
                time.sleep(0.25)
        control.save_text(directory / (role + ".release"), "release\n")
        deadline = time.monotonic() + 180
        while not (directory / (role + ".completed")).exists():
            value = inspect(identifier)
            if not value["State"]["Running"]:
                raise RuntimeError("helper exited before completing " + role)
            if time.monotonic() > deadline:
                raise TimeoutError(role + " exceeded bounded checkpoint time")
            time.sleep(0.25)
        control.save_text(directory / (role + ".finish"), "finish\n")
        control.call(["docker", "wait", identifier], timeout=20)
        value = inspect(identifier)
        control.save(directory / (role + "-ended.json"), control.state(value))
        stopped(value)
    except BaseException as error:
        primary = error
    finally:
        errors = finish_helper(identifier, directory, role, sampler)
    if primary is not None:
        for error in errors:
            primary.add_note("Checkpoint cleanup: " + repr(error))
        raise primary
    if errors:
        raise errors[0]


def run():
    context = control.local_docker()
    services = original_services()
    source = control.data_volume(services["storage"])
    (volume,) = json.loads(control.call(["docker", "volume", "inspect", source]))
    validate_volume(volume, source)
    if volume_users(source) != {services["storage"]["Id"]}:
        raise RuntimeError("source has an unexpected live volume user")
    (image,) = json.loads(control.call(["docker", "image", "inspect", IMAGE]))
    if image["Config"].get("Labels", {}).get("org.examples.owner") != control.PROJECT:
        raise RuntimeError("checkpoint utility image is not example-owned")
    run_id = uuid.uuid4()
    directory = cleanup.regular(
        cleanup.results_root() / "cold-storage" / str(run_id), cleanup.results_root()
    )
    directory.mkdir(parents=True)
    restore_name = "iceberg-cold-" + run_id.hex
    frozen = control.sources()
    control.save(
        directory / "invocation.json",
        {
            "run_id": str(run_id),
            "context": context,
            "source_sha256": frozen,
            "source_volume": volume,
            "restore_volume": restore_name,
            "utility_image": image["Id"],
            "services": {r: control.state(v) for r, v in services.items()},
            "scope": "Cold checkpoint and byte/metadata restore only; native database readback pending. Original failed recovery verdicts unchanged.",
        },
    )
    error, restart_errors = None, []
    try:
        for role in ("clickhouse", "storage"):
            value = services[role]
            control.call(["docker", "stop", "--time", "30", value["Id"]], timeout=40)
            ended = control.owned(value["Id"], role)
            control.save(directory / (role + "-stopped.json"), control.state(ended))
            stopped(ended)
        if volume_users(source):
            raise RuntimeError("source still has a live owner")
        helper(
            directory,
            run_id,
            "backup",
            image["Id"],
            ["--mount", "type=volume,src=" + source + ",dst=/source,readonly"],
            BACKUP,
        )
        archive = directory / "storage.tar.gz"
        with archive.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != (directory / "archive.sha256").read_text().split()[0]:
            raise RuntimeError("external archive checksum differs")
        control.save(
            directory / "archive-proof.json",
            {
                "sha256": checksum,
                "bytes": archive.stat().st_size,
                "files_manifest_sha256": hashlib.sha256(
                    (directory / "files.sha256").read_bytes()
                ).hexdigest(),
                "metadata_manifest_sha256": hashlib.sha256(
                    (directory / "metadata.txt").read_bytes()
                ).hexdigest(),
            },
        )
        for role, value in services.items():
            stopped(control.owned(value["Id"], role))
        if volume_users(source):
            raise RuntimeError("source fencing changed during checkpoint")
        control.call(
            [
                "docker",
                "volume",
                "create",
                "--label",
                LABEL + "=" + str(run_id),
                restore_name,
            ]
        )
        (restored,) = json.loads(
            control.call(["docker", "volume", "inspect", restore_name])
        )
        if (
            restored["Labels"].get(LABEL) != str(run_id)
            or restored["Name"] != restore_name
        ):
            raise RuntimeError("restore volume ownership differs")
        control.save(directory / "restore-volume.json", restored)
        helper(
            directory,
            run_id,
            "restore",
            image["Id"],
            ["--mount", "type=volume,src=" + restore_name + ",dst=/restore"],
            RESTORE,
        )
        if control.sources() != frozen:
            raise RuntimeError("controller sources changed during checkpoint")
    except BaseException as caught:
        error = caught
    finally:
        for role in ("storage", "clickhouse"):
            try:
                value = control.owned(services[role]["Id"], role)
                if not value["State"]["Running"]:
                    control.call(["docker", "start", value["Id"]])
                deadline = time.monotonic() + 90
                while True:
                    value = control.owned(value["Id"], role)
                    if value["State"].get("Health", {}).get("Status") == "healthy":
                        break
                    if not value["State"]["Running"] or time.monotonic() > deadline:
                        raise RuntimeError("original service did not recover")
                    time.sleep(0.5)
                control.save(
                    directory / (role + "-restarted.json"), control.state(value)
                )
            except BaseException as caught:
                restart_errors.append({role: repr(caught)})
        control.save(
            directory / "result.json",
            {
                "passed": error is None and not restart_errors,
                "native_restore_verified": False,
                "run_id": str(run_id),
                "restore_volume": restore_name,
                "error": repr(error) if error else None,
                "restart_errors": restart_errors,
                "completed_ns": time.time_ns(),
            },
        )
    print(directory.relative_to(control.ROOT), flush=True)
    if error:
        raise error
    if restart_errors:
        raise RuntimeError("original service restart failed")
    return directory


def controller_lock():
    results = cleanup.results_root()
    results.mkdir(exist_ok=True)
    path = cleanup.regular(results / "controller.lock", results)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    return os.fdopen(descriptor, "a")


def main():
    with controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()


if __name__ == "__main__":
    main()
