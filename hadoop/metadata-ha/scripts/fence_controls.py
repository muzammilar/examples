"""Reject unowned targets and wrong RPC ports without invoking SSH."""

import json
import lab

for host, port in (("unowned-host", "9000"), ("nn1", "9001")):
    result = lab.command(
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "source_host=" + host,
        "-e",
        "source_port=" + port,
        "--entrypoint",
        "bash",
        "zkfc1",
        "/opt/hadoop/fence.sh",
        check=False,
        timeout=20,
    )
    if result.returncode != 1:
        raise RuntimeError("Unexpected fencing allowlist result: " + str(result))
print(json.dumps({"unowned_target_rejected": True, "wrong_port_rejected": True}))
