"""Repair the diagnosed no-op-authorize bootstrap bug without touching stored data."""

import base64
import json
from pathlib import Path
import sys
from guest import ssh

expected = sys.argv[1]
if (
    json.loads(Path("/state/bootstrap-plan.json").read_text())["fsid"] != expected
    or ssh(0, "ceph fsid", True).strip() != expected
):
    raise RuntimeError("Refusing another cluster")
for name in ("lab-writer", "lab-reader"):
    key = ssh(0, "ceph auth get client." + name, True)
    if "[client." + name + "]" not in key or "key = " not in key:
        raise RuntimeError("No valid keyring returned")
    encoded = base64.b64encode(key.encode()).decode()
    for node in range(3):
        ssh(
            node,
            "printf %s "
            + encoded
            + " | base64 -d > /etc/ceph/ceph.client."
            + name
            + ".keyring; chmod 600 /etc/ceph/ceph.client."
            + name
            + ".keyring",
        )
print(
    json.dumps(
        {
            "fsid": expected,
            "clients_repaired": 2,
            "guest_copies": 6,
            "data_reset": False,
        }
    )
)
