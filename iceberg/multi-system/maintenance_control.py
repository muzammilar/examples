"""Run the current maintenance engine on fresh, network-isolated tmpfs storage."""

import hashlib
import json
import os
import signal
import subprocess
import time
import uuid

import compactor_control
import recovery_cleanup as cleanup
import recovery_control as control

IMAGE = "iceberg-multi-compactor-current:dev"
LABEL = "org.examples.maintenance-smoke"


def owned(identifier, image, name, run_id, directory):
    value = json.loads(control.call(["docker", "inspect", identifier]))[0]
    host, labels = value["HostConfig"], value["Config"]["Labels"]
    mounts = {
        (m["Type"], m["Source"], m["Destination"], m["RW"]) for m in value["Mounts"]
    }
    expected = {
        ("bind", str(directory), "/results", True),
        (
            "bind",
            str(directory / "maintenance_smoke.py"),
            "/opt/lab/maintenance_smoke.py",
            False,
        ),
    }
    if (
        value["Id"] != identifier
        or value["Image"] != image
        or value["Name"] != "/" + name
        or labels.get(LABEL) != run_id
        or any(
            labels.get("com.docker.compose." + key)
            for key in ("project", "service", "project.working_dir")
        )
        or host["NetworkMode"] != "none"
        or host["NanoCpus"] != 2_000_000_000
        or host["Memory"] != 2 * control.GIB
        or host["MemorySwap"] != 2 * control.GIB
        or not host["ReadonlyRootfs"]
        or host["Privileged"]
        or len(value["Mounts"]) != 2
        or mounts != expected
        or host["Tmpfs"]
        != {
            "/data": "rw,size=536870912,mode=1777",
            "/tmp": "rw,exec,size=536870912,mode=1777",
        }
    ):
        raise RuntimeError("refuse action on unproven maintenance probe")
    return value


def run():
    context = control.local_docker()
    root = cleanup.results_root() / "maintenance-current" / str(uuid.uuid4())
    root.mkdir(parents=True)
    run_id, name = root.name, "iceberg-maintenance-smoke-" + root.name.replace("-", "")
    (image,) = json.loads(control.call(["docker", "image", "inspect", IMAGE]))
    if image["Config"]["Labels"].get("org.examples.owner") != control.PROJECT:
        raise RuntimeError("current maintenance image lacks example ownership")
    image_id = image["Id"]
    frozen = {}
    for filename in (
        "maintenance_smoke.py",
        "maintenance_control.py",
        "docker-compose.maintenance-current.yml",
    ):
        source = cleanup.regular(control.ROOT / filename, control.ROOT)
        data = source.read_bytes()
        control.save_text(root / filename, data.decode())
        frozen[filename] = hashlib.sha256(data).hexdigest()
    expected_sources = {
        "iceberg-compaction/" + p.name: cleanup.digest(p)
        for p in (control.ROOT / "iceberg-compaction").iterdir()
        if p.is_file()
    }
    command = [
        "docker",
        "create",
        "--name",
        name,
        "--label",
        LABEL + "=" + run_id,
        "--label",
        "com.docker.compose.project=",
        "--label",
        "com.docker.compose.service=",
        "--label",
        "com.docker.compose.project.working_dir=",
        "--network",
        "none",
        "--hostname",
        "localhost",
        "--cpus=2",
        "--memory=2g",
        "--memory-swap=2g",
        "--read-only",
        "--tmpfs",
        "/data:rw,size=536870912,mode=1777",
        "--tmpfs",
        "/tmp:rw,exec,size=536870912,mode=1777",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,source={root},target=/results",
        "--mount",
        f"type=bind,source={root / 'maintenance_smoke.py'},target=/opt/lab/maintenance_smoke.py,readonly",
        "--env",
        "AWS_EC2_METADATA_DISABLED=true",
        "--env",
        "SPARK_LOCAL_IP=127.0.0.1",
        "--entrypoint",
        "sh",
        image_id,
        "-c",
        compactor_control.WAIT,
        "sh",
        "/results/START",
        "/opt/spark/bin/spark-submit",
        "--master",
        "local[2]",
        "--driver-memory",
        "1g",
        "--conf",
        "spark.driver.extraJavaOptions=-Duser.home=/tmp",
        "/opt/lab/maintenance_smoke.py",
    ]
    control.save(
        root / "invocation.json",
        {
            "command": command,
            "context": context,
            "sources": frozen,
            "image_sources": expected_sources,
            "image_id": image_id,
            "started_ns": time.time_ns(),
            "scope": "Offline Hadoop-catalog correctness, not S3/live fault qualification or a capacity benchmark",
        },
    )
    identifier = None
    try:
        identifier = control.call(command).strip()
        control.save(
            root / "created.json", owned(identifier, image_id, name, run_id, root)
        )
        control.call(["docker", "start", identifier])
        value = owned(identifier, image_id, name, run_id, root)
        compactor_control.helper_sources(
            value, "recovery-compactor", {"source_sha256": expected_sources}
        )
        control.save(root / "gated.json", value)
        control.save_text(root / "START", "start\n")
        with (root / "console.log").open("x") as log:
            subprocess.run(
                ["docker", "logs", "--follow", identifier],
                env=control.DOCKER_ENV,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600,
                check=True,
            )
        final = owned(identifier, image_id, name, run_id, root)
        control.save(root / "final.json", final)
        state = final["State"]
        if state["Running"] or state["Pid"] or state["ExitCode"] or state["OOMKilled"]:
            raise RuntimeError("maintenance probe did not exit cleanly")
        report = cleanup.load(root / "result.json", root)
        if report["passed"] is not True:
            raise RuntimeError("maintenance compatibility check failed")
        if any(
            cleanup.digest(control.ROOT / name) != sha for name, sha in frozen.items()
        ):
            raise RuntimeError("maintenance sources changed during run")
        owned(identifier, image_id, name, run_id, root)
        control.call(["docker", "rm", identifier])
        if (
            identifier
            in control.call(["docker", "ps", "--all", "--no-trunc", "-q"]).split()
        ):
            raise RuntimeError("probe container remains after removal")
        control.save(
            root / "cleanup.json", {"container_removed": True, "id": identifier}
        )
        control.save(
            root / "qualification.json",
            {
                "passed": True,
                "result_sha256": cleanup.digest(root / "result.json"),
                "sources": frozen,
                "image_id": image_id,
                "completed_ns": time.time_ns(),
            },
        )
    except BaseException as primary:
        try:
            control.save(
                root / "failure.json",
                {
                    "passed": False,
                    "error": repr(primary),
                    "completed_ns": time.time_ns(),
                },
            )
            if identifier is None:
                matches = control.call(
                    [
                        "docker",
                        "ps",
                        "--all",
                        "--no-trunc",
                        "-q",
                        "--filter",
                        "name=^/" + name + "$",
                        "--filter",
                        "label=" + LABEL + "=" + run_id,
                    ]
                ).split()
                if not matches:
                    control.save(
                        root / "creation-absent.json",
                        {"healthy_scoped_listing": True, "name": name},
                    )
                    raise RuntimeError("no owned container found after failed create")
                if len(matches) != 1:
                    raise RuntimeError("ambiguous maintenance creation identity")
                identifier = matches[0]
            value = owned(identifier, image_id, name, run_id, root)
            control.save(root / "failure-inspect.json", value)
            if value["State"]["Running"]:
                control.call(["docker", "stop", "--time", "20", identifier], timeout=30)
                stopped = owned(identifier, image_id, name, run_id, root)
                control.save(root / "failure-stopped.json", stopped)
                if stopped["State"]["Running"] or stopped["State"]["Pid"]:
                    raise RuntimeError("failed probe was not fenced")
            control.save_text(
                root / "failure-console.log",
                control.call(["docker", "logs", identifier]),
            )
        except BaseException as diagnostic:
            primary.add_note(
                "Could not complete positive failure inspection: " + repr(diagnostic)
            )
        raise
    print(root.relative_to(control.ROOT))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, control.interrupt_controller)
    run()
