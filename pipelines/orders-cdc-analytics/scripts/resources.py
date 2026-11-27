"""Owned-volume allocation plus native per-role I/O operation counters."""

import json
import subprocess
import lab


def main():
    path = lab.directory("resources")
    names = subprocess.check_output(
        [
            "docker",
            "volume",
            "ls",
            "--filter",
            "label=com.docker.compose.project=" + lab.PROJECT,
            "--quiet",
        ],
        text=True,
    ).split()
    if not names:
        raise RuntimeError("No owned volume set")
    metadata = json.loads(
        subprocess.check_output(["docker", "volume", "inspect", *names], text=True)
    )
    if any(
        row["Labels"].get("com.docker.compose.project") != lab.PROJECT
        for row in metadata
    ):
        raise RuntimeError("Unowned volume")
    mounts = [
        arg
        for name in names
        for arg in (
            "--mount",
            "type=volume,source=" + name + ",target=/storage/" + name + ",readonly",
        )
    ]
    output = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "DAC_READ_SEARCH",
            "--cpus",
            ".1",
            "--memory",
            "64m",
            *mounts,
            "examples/orders-cdc-workload:1",
            "volumes",
        ],
        text=True,
    )
    (path / "volumes.jsonl").write_text(output)
    counters = {}
    for role in lab.ROLES:
        identifiers = lab.command("ps", "--quiet", role).split()
        for identifier in identifiers:
            lab.assert_owned(identifier, role)
            args = ["docker", "exec", identifier]
            if role == "orders":
                args += ["/orders", "cgroup"]
            else:
                args += [
                    "sh",
                    "-c",
                    "for f in cpu.stat memory.peak memory.current io.stat; do echo METRIC:$f; cat /sys/fs/cgroup/$f; done",
                ]
            result = subprocess.run(args, capture_output=True, text=True, timeout=15)
            counters[identifier] = {
                "role": role,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
    lab.save(path / "native-cgroups.json", counters)
    lab.save(path / "status.json", {"passed": True})
    print(path)


if __name__ == "__main__":
    main()
