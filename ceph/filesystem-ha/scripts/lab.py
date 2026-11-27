"""Compose commands scoped to the owned CephFS example."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--project-name", "ceph-filesystem-ha"]


def command(*args, timeout=600, check=True):
    result = subprocess.run(
        [*COMPOSE, *args], cwd=ROOT, text=True, capture_output=True, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result


def native(script, *args, timeout=900):
    return command(
        "run", "--rm", "control", "/opt/lab/native/" + script, *args, timeout=timeout
    )
