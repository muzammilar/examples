"""Three independent Linux kernels on an isolated, unprivileged QEMU multicast LAN."""

import json
from pathlib import Path
import signal
import subprocess
import time

state = Path("/state")
state.mkdir(exist_ok=True)
key = state / "id_ed25519"
if not key.exists():
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
pub = key.with_suffix(".pub").read_text().strip()


def command(i):
    node = f"ceph{i}"
    root = state / node
    root.mkdir(exist_ok=True)
    if not (root / "os.qcow2").exists():
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
                str(root / "os.qcow2"),
                "16G",
            ],
            check=True,
        )
        for d in range(4):
            subprocess.run(
                [
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    str(root / f"disk{d}.qcow2"),
                    "12G",
                ],
                check=True,
            )
        cfg = {
            "hostname": node,
            "manage_etc_hosts": True,
            "ssh_pwauth": False,
            "disable_root": False,
            "bootcmd": [
                [
                    "bash",
                    "-ec",
                    "sed -i '/pam_motd[.]so/s/^/# cephlab: /' /etc/pam.d/sshd",
                ]
            ],
            "users": [
                {"name": "root", "ssh_authorized_keys": [pub]},
                {
                    "name": "lab",
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "shell": "/bin/bash",
                    "ssh_authorized_keys": [pub],
                },
            ],
            "runcmd": [
                [
                    "bash",
                    "-ec",
                    'export DEBIAN_FRONTEND=noninteractive; mkdir -p /mnt/packages; mount -o ro /dev/disk/by-label/CEPHPKGS /mnt/packages; printf "#!/bin/sh\\nexit 101\\n" > /usr/sbin/policy-rc.d; chmod +x /usr/sbin/policy-rc.d; apt-get update > /var/log/ceph-packages.log 2>&1; apt-get install -y --no-install-recommends --no-remove /mnt/packages/*.deb >> /var/log/ceph-packages.log 2>&1; apt-get check >> /var/log/ceph-packages.log 2>&1; rm /usr/sbin/policy-rc.d; touch /var/lib/cephlab-ready',
                ]
            ],
        }
        (root / "user-data").write_text("#cloud-config\n" + json.dumps(cfg))
        (root / "meta-data").write_text(
            json.dumps({"instance-id": node, "local-hostname": node})
        )
        network = {
            "version": 2,
            "ethernets": {
                "cluster": {
                    "match": {"macaddress": f"52:54:00:01:00:{i + 1:02x}"},
                    "set-name": "cluster",
                    "addresses": [f"192.168.65.{11 + i}/24"],
                },
                "mgmt": {
                    "match": {"macaddress": f"52:54:00:02:00:{i + 1:02x}"},
                    "set-name": "mgmt",
                    "dhcp4": True,
                },
            },
        }
        (root / "network").write_text(json.dumps(network))
        subprocess.run(
            [
                "cloud-localds",
                "--network-config",
                str(root / "network"),
                str(root / "seed.img"),
                str(root / "user-data"),
                str(root / "meta-data"),
            ],
            check=True,
        )
    args = [
        "qemu-system-aarch64",
        "-machine",
        "virt",
        "-cpu",
        "max,pauth=off",
        "-accel",
        "tcg,thread=multi",
        "-smp",
        "3",
        "-m",
        "5120",
        "-nographic",
        "-bios",
        "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd",
        "-qmp",
        f"unix:{root}/qmp.sock,server=on,wait=off",
        "-drive",
        f"file={root}/os.qcow2,if=virtio,format=qcow2,cache=none",
        "-drive",
        f"file={root}/seed.img,if=virtio,format=raw,readonly=on",
        "-drive",
        "file=/opt/packages.iso,if=virtio,format=raw,readonly=on",
        "-netdev",
        "socket,id=lan,mcast=230.0.0.1:12345",
        "-device",
        f"virtio-net-pci,netdev=lan,mac=52:54:00:01:00:{i + 1:02x},romfile=",
        "-netdev",
        f"user,id=mgmt,hostfwd=tcp:0.0.0.0:{2221 + i}-:22,hostfwd=tcp:0.0.0.0:{9001 + i}-:9000",
        "-device",
        f"virtio-net-pci,netdev=mgmt,mac=52:54:00:02:00:{i + 1:02x},romfile=",
        "-device",
        "virtio-scsi-pci,id=scsi",
    ]
    replacement_file = root / "replacements.json"
    replacements = (
        json.loads(replacement_file.read_text()) if replacement_file.exists() else {}
    )
    for d in range(3):
        actual = replacements.get(str(d), d)
        serial = f"cephlab-{node}-r{d}" if actual != d else f"cephlab-{node}-d{d}"
        args += [
            "-drive",
            f"file={root}/disk{actual}.qcow2,if=none,id=disk{d},format=qcow2,cache=none",
            "-device",
            f"scsi-hd,drive=disk{d},bus=scsi.0,id=data{d},serial={serial}",
        ]
    return args


children = {}
logs = {}
stop = False


def stopping(*_):
    global stop
    stop = True


signal.signal(signal.SIGTERM, stopping)
signal.signal(signal.SIGINT, stopping)
while not stop:
    for i in range(3):
        p = children.get(i)
        if p and p.poll() is not None:
            logs[i].close()
            del children[i]
        if i not in children and not (state / f"pause{i}").exists():
            logs[i] = (
                (state / f"ceph{i}" / "console.log")
                if (state / f"ceph{i}").exists()
                else None
            )
            args = command(i)
            logs[i] = (state / f"ceph{i}" / "console.log").open("a")
            children[i] = subprocess.Popen(
                args, stdout=logs[i], stderr=subprocess.STDOUT
            )
    time.sleep(1)
for p in children.values():
    p.terminate()
for p in children.values():
    p.wait(timeout=30)
