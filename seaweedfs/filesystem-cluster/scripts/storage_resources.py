"""Owned-volume bytes and selected native cgroup lifetime counters."""

import json
import subprocess
import time

import control as c


def main():
    roles = [f"{kind}{i}" for kind in ("master", "etcd", "volume") for i in range(3)]
    names = ["seaweedfs-filesystem-cluster_" + role for role in roles]
    values = json.loads(
        subprocess.check_output(["docker", "volume", "inspect", *names], text=True)
    )
    if len(values) != 9 or any(
        v["Labels"].get("com.docker.compose.project") != "seaweedfs-filesystem-cluster"
        for v in values
    ):
        raise RuntimeError("Volume ownership differs from this example")
    mounts = [
        argument
        for name, role in zip(names, roles)
        for argument in [
            "--mount",
            f"type=volume,source={name},target=/storage/{role},readonly",
        ]
    ]
    program = """import os,json,pathlib
result={}
for directory in pathlib.Path('/storage').iterdir():
    stats=[p.stat() for p in directory.rglob('*') if p.is_file()]
    result[directory.name]={'files':len(stats),'apparent_bytes':sum(s.st_size for s in stats),'allocated_bytes':sum(s.st_blocks*512 for s in stats)}
print(json.dumps(result))"""
    volume_bytes = json.loads(
        subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cpus",
                ".1",
                "--memory",
                "64m",
                *mounts,
                "--entrypoint",
                "python3",
                "seaweedfs-filesystem-workload:4.55",
                "-c",
                program,
            ],
            text=True,
        )
    )
    counters = {}
    for role in [*c.STORAGE, "files-writer", "files-reader"]:
        command = "for f in cpu.stat memory.peak io.stat; do echo METRIC:$f; cat /sys/fs/cgroup/$f; done; if test -d /tmp/weed-cache; then echo METRIC:cache_bytes; du -sb /tmp/weed-cache; fi"
        result = subprocess.run(
            [*c.COMPOSE, "exec", "-T", role, "sh", "-c", command],
            cwd=c.ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
        counters[role] = {
            "counters": result.stdout,
            "collector_exit_code": result.returncode,
            "measurement_error": result.stderr[:250] or None,
        }
    path = c.ROOT / "results" / ("storage-resources-" + str(time.time_ns()) + ".json")
    c.save(
        path,
        {
            "volumes": volume_bytes,
            "native_cgroups": counters,
            "scope": "Cumulative container lifetime counters and non-atomic current owned-volume sizes; no host data paths. Distroless etcd lacks a shell; its native cgroup probe is unavailable, while Docker sampling and read-only volume sizing remain valid.",
        },
    )
    print(path)


if __name__ == "__main__":
    main()
