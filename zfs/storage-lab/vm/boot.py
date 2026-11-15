"""Boot a disposable guest kernel. No host devices, modules or privileges."""

import json
import os
import subprocess
from pathlib import Path

state = Path("/state")
state.mkdir(exist_ok=True)
if not (state / "os.qcow2").exists():
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            "/opt/base.qcow2",
            str(state / "os.qcow2"),
            "12G",
        ],
        check=True,
    )
    disk_gib = int(os.environ.get("DISK_GIB", "16"))
    if not 8 <= disk_gib <= 64:
        raise ValueError("DISK_GIB must be 8..64")
    for disk in tuple(f"data{i}" for i in range(6)):
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                str(state / (disk + ".qcow2")),
                f"{disk_gib}G",
            ],
            check=True,
        )
    (state / "layout.json").write_text(json.dumps({"version": 2, "disk_gib": disk_gib}))
    pub = Path("/keys/id_ed25519.pub").read_text().strip()
    # JSON is valid YAML; only owned VM disks are provisioned.
    cfg = {
        "hostname": os.environ["NODE"],
        "ssh_pwauth": False,
        "bootcmd": [
            ["bash", "-ec", "sed -i '/pam_motd[.]so/s/^/# zfslab: /' /etc/pam.d/sshd"]
        ],
        "users": [
            {
                "name": "lab",
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
                "ssh_authorized_keys": [pub],
            }
        ],
        "runcmd": [
            [
                "bash",
                "-ec",
                "modprobe zfs; touch /run/zfslab-ready",
            ]
        ],
    }
    (state / "user-data").write_text("#cloud-config\n" + json.dumps(cfg))
    (state / "meta-data").write_text(
        json.dumps(
            {"instance-id": os.environ["NODE"], "local-hostname": os.environ["NODE"]}
        )
    )
    subprocess.run(
        [
            "cloud-localds",
            str(state / "seed.img"),
            str(state / "user-data"),
            str(state / "meta-data"),
        ],
        check=True,
    )
layout = (
    json.loads((state / "layout.json").read_text())
    if (state / "layout.json").exists()
    else None
)
if layout != {"version": 2, "disk_gib": int(os.environ.get("DISK_GIB", "16"))}:
    raise RuntimeError(
        "VM disk layout changed or is legacy; use the complete clean lifecycle"
    )
args = [
    "qemu-system-aarch64",
    "-machine",
    "virt",
    "-cpu",
    "max",
    "-accel",
    "tcg,thread=multi",
    "-smp",
    "2",
    "-m",
    "1536",
    "-nographic",
    "-bios",
    "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd",
    "-drive",
    "file=/state/os.qcow2,if=virtio,format=qcow2,cache=none",
    "-drive",
    "file=/state/seed.img,if=virtio,format=raw,readonly=on",
    "-netdev",
    "user,id=n0,hostfwd=tcp:0.0.0.0:2222-:22",
    "-device",
    "virtio-net-pci,netdev=n0,romfile=",
]
for i in range(6):
    args += [
        "-drive",
        f"file=/state/data{i}.qcow2,if=none,id=data{i},format=qcow2,cache=none",
        "-device",
        f"virtio-blk-pci,drive=data{i},serial=zfslab-d{i}",
    ]
os.execvp(args[0], args)
