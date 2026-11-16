"""Keep kubelet on kind's network when a second Docker network is attached."""

import ipaddress
import json
from pathlib import Path
import shlex
import subprocess
import sys

cluster = "greptime-operator"
nodes = subprocess.check_output(
    ["kind", "get", "nodes", "--name", cluster], text=True
).split()
selected = sys.argv[1:] or nodes
if not selected or any(node not in nodes for node in selected):
    raise RuntimeError("expected nodes owned by this example")
proof = []
for node in selected:
    info = json.loads(subprocess.check_output(["docker", "inspect", node], text=True))[
        0
    ]
    if info["Config"]["Labels"].get("io.x-k8s.kind.cluster") != cluster:
        raise RuntimeError("refusing foreign node")
    address = str(
        ipaddress.ip_address(info["NetworkSettings"]["Networks"]["kind"]["IPAddress"])
    )
    path = "/var/lib/kubelet/kubeadm-flags.env"
    before = subprocess.check_output(["docker", "exec", node, "cat", path], text=True)
    lines = before.splitlines()
    entries = [line for line in lines if line.startswith("KUBELET_KUBEADM_ARGS=")]
    if len(entries) != 1:
        raise RuntimeError("unexpected kubelet argument file")
    (value,) = shlex.split(entries[0].split("=", 1)[1])
    flags = [flag for flag in shlex.split(value) if not flag.startswith("--node-ip=")]
    flags.append("--node-ip=" + address)
    after = (
        "\n".join(
            "KUBELET_KUBEADM_ARGS=" + shlex.quote(" ".join(flags))
            if line == entries[0]
            else line
            for line in lines
        )
        + "\n"
    )
    if after != before:
        subprocess.run(
            ["docker", "exec", "-i", node, "tee", path],
            input=after,
            text=True,
            stdout=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            ["docker", "exec", node, "systemctl", "restart", "kubelet"], check=True
        )
    proof.append(
        {
            "node": node,
            "kind_address": address,
            "changed": after != before,
            "before": before,
            "after": after,
        }
    )
Path("results").mkdir(exist_ok=True)
Path("results/pinned-node-addresses.json").write_text(
    json.dumps(proof, indent=2) + "\n"
)
print(json.dumps(proof, indent=2))
