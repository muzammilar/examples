"""Native three-zone volume replacement and consistency checks."""

import os
import subprocess

TARGET = os.environ.get("FAULT_TARGET", "volume0")
if TARGET not in {"volume0", "etcd0", "master0", "filer0", "s3api0"}:
    raise ValueError("target is not allowlisted")
DISK = "/data"


def audit(mode):
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "python",
            "tools",
            "/app/seaweed_audit.py",
            mode,
        ],
        check=True,
    )


def prepare_empty(project, mounts):
    pass


def shell(commands):
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "master0",
            "weed",
            "shell",
            "-master=master0:9333,master1:9333,master2:9333",
            "-filer=filer0:8888",
        ],
        input=commands,
        text=True,
        check=True,
    )


def repair():
    shell("lock\nvolume.fix.replication -apply\nunlock\n")
