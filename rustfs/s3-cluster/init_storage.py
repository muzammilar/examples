"""Prepare only the four example-owned native drive roots."""

import os
from pathlib import Path

for index in range(4):
    root = Path("/disks") / str(index)
    drive = root / "disk0"
    drive.mkdir(exist_ok=True)
    os.chown(root, 10001, 10001)
    os.chown(drive, 10001, 10001)
