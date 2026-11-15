"""Native protected-object heal plus independent reconstructed-shard fingerprints."""

import os
import subprocess

TARGET = "rustfs0"
DISK = "/data/disk0"


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True)


def repair():
    compose("run", "--rm", "--entrypoint", "python", "tools", "/app/rustfs_admin.py")


def audit(mode):
    compose("run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "audit", mode)


def prepare_empty(project, mounts):
    name = next(m["Name"] for m in mounts if m["Destination"] == DISK)
    subprocess.run(
        [
            "docker",
            "volume",
            "create",
            "--label",
            "com.docker.compose.project=" + project,
            "--label",
            "com.docker.compose.volume=data00",
            name,
        ],
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            name + ":/empty",
            "--entrypoint",
            "python",
            project + "-tools:dev",
            "-c",
            'import os; os.chown("/empty",10001,10001)',
        ],
        check=True,
    )


def release_disk_references():
    # A completed Compose init job still holds Docker volume references.
    compose("rm", "-f", "init")
