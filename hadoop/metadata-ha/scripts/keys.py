"""Create lab-only fencing identities in an owned secret volume, never Git/images."""

import os
from pathlib import Path
import subprocess

root = Path("/lab-ssh")
root.mkdir(exist_ok=True)
for name in ("id_ecdsa", "nn1-hostkey", "nn2-hostkey"):
    path = root / name
    if not path.exists():
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ecdsa",
                "-b",
                "256",
                "-m",
                "PEM",
                "-N",
                "",
                "-f",
                str(path),
            ],
            check=True,
        )
    os.chown(path, 10000, 10000)
    os.chmod(path, 0o600)
authorized = root / "authorized_keys"
if not authorized.exists():
    authorized.write_text(
        "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty "
        + (root / "id_ecdsa.pub").read_text()
    )
os.chown(authorized, 10000, 10000)
os.chmod(authorized, 0o600)
os.chown(root, 10000, 10000)
os.chmod(root, 0o700)

known = root / "known_hosts"
known.write_text(
    "".join(
        "[" + node + "]:2222 " + (root / (node + "-hostkey.pub")).read_text()
        for node in ("nn1", "nn2")
    )
)
os.chown(known, 10000, 10000)
os.chmod(known, 0o600)
