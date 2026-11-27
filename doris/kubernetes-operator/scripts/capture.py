"""Capture native settings, storage and cgroups without changing database state."""

import json
import time

import kube


def main():
    directory = kube.ROOT / "results" / ("native-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    pods = kube.get("pods")
    (directory / "pods.json").write_text(json.dumps(pods, indent=2) + "\n")
    (directory / "nodes.json").write_text(
        json.dumps(kube.get("nodes"), indent=2) + "\n"
    )
    (directory / "cluster.json").write_text(
        json.dumps(kube.get("doriscluster", "orders"), indent=2) + "\n"
    )
    (directory / "versions.json").write_text(kube.run("version", "-o", "json").stdout)
    for name, sql in {
        "frontends": "SHOW FRONTENDS",
        "backends": "SHOW BACKENDS",
        "tablets": "SHOW TABLETS FROM lab.orders",
        "data": "SHOW DATA FROM lab.orders",
        "table": "SHOW CREATE TABLE lab.orders",
        "version": "SELECT VERSION()",
    }.items():
        result = kube.query(sql)
        (directory / (name + ".tsv")).write_text(result.stdout)
    for pod in pods["items"]:
        role = pod["metadata"].get("labels", {}).get("lab.example/role")
        if role not in ("frontend", "backend"):
            continue
        name = pod["metadata"]["name"]
        path = (
            "/opt/apache-doris/fe/doris-meta"
            if role == "frontend"
            else "/opt/apache-doris/be/storage"
        )
        shell = 'for f in cpu.stat memory.current memory.peak io.stat; do echo "$f"; cat /sys/fs/cgroup/"$f"; done'
        cgroup = kube.run("exec", name, "--", "bash", "-c", shell)
        (directory / (name + "-cgroup.txt")).write_text(cgroup.stdout)
        disk = kube.run(
            "exec",
            name,
            "--",
            "bash",
            "-c",
            'du -sb "$1"; du -sk "$1"',
            "storage",
            path,
        )
        (directory / (name + "-disk.txt")).write_text(disk.stdout)
        limits = kube.run(
            "exec",
            name,
            "--",
            "bash",
            "-c",
            'printf "max_map_count="; cat /proc/sys/vm/max_map_count; printf "file_limit="; ulimit -n',
        )
        (directory / (name + "-kernel.txt")).write_text(limits.stdout)
        config = (
            "/opt/apache-doris/fe/conf/fe.conf"
            if role == "frontend"
            else "/opt/apache-doris/be/conf/be.conf"
        )
        (directory / (name + "-config.txt")).write_text(
            kube.run("exec", name, "--", "cat", config).stdout
        )
        if role == "backend":
            native = kube.run(
                "exec",
                name,
                "--",
                "curl",
                "--fail",
                "--silent",
                "http://127.0.0.1:8040/api/show_config",
            )
            (directory / (name + "-effective-config.json")).write_text(native.stdout)
    print(str(directory))


if __name__ == "__main__":
    main()
