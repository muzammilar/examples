"""Native Garage operations for the shared external-ACK fault harness."""

import os
import subprocess

TARGET = "garage0"
DISK = "/data"


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True)


def audit(mode):
    compose("run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "audit", mode)


def prepare_empty(project, mounts):
    meta = next(m["Name"] for m in mounts if m["Destination"] == "/meta")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            meta + ":/meta",
            "--entrypoint",
            "python",
            project + "-tools:dev",
            "-c",
            "from pathlib import Path; Path('/meta/data_layout').unlink(missing_ok=True)",
        ],
        check=True,
    )


def repair():
    # An all-nodes RPC can print a peer error yet exit zero during reconnection.
    # Execute locally on each node so every launch has a checked exit status.
    for i in range(9):
        compose("exec", "-T", f"garage{i}", "/garage", "repair", "--yes", "tables")
    for i in range(9):
        compose("exec", "-T", f"garage{i}", "/garage", "repair", "--yes", "blocks")
