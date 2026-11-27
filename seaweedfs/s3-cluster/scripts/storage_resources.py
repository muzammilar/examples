"""Read native cgroup and /data counters inside only the inspected owned roles."""

import json
import subprocess

import control as c

inventory = c.inventory()
rows = {}
for role, info in inventory.items():
    # No host paths/devices, mount helpers, credentials or global Docker pruning.
    code = 'for f in cpu.stat memory.peak io.stat; do echo "METRIC:$f"; cat "/sys/fs/cgroup/$f"; done; echo STORAGE_ALLOCATED_KIB; du -sk /data; echo STORAGE_APPARENT_BYTES; du -sb /data'
    result = subprocess.run(
        [*c.COMPOSE, "exec", "-T", role, "sh", "-c", code],
        text=True,
        capture_output=True,
        timeout=20,
    )
    sections = {}
    current = None
    for line in result.stdout.splitlines():
        if line.startswith("METRIC:") or line.startswith("STORAGE_"):
            current = line.removeprefix("METRIC:")
            sections[current] = []
        elif current:
            sections[current].append(line)
    cpu = {
        key: int(value)
        for line in sections.get("cpu.stat", [])
        for key, value in [line.split()]
    }
    io = []
    for line in sections.get("io.stat", []):
        fields = line.split()
        io.append(
            {
                "device": fields[0],
                **{k: int(v) for field in fields[1:] for k, v in [field.split("=")]},
            }
        )
    scalar = lambda key: int(sections[key][0].split()[0]) if sections.get(key) else None
    rows[role] = {
        "container_id": info["Id"],
        "started_at": info["State"]["StartedAt"],
        "cpu_stat": cpu or None,
        "memory_peak_bytes": scalar("memory.peak"),
        "io_stat": io or None,
        "data_allocated_kib": scalar("STORAGE_ALLOCATED_KIB"),
        "data_apparent_bytes": scalar("STORAGE_APPARENT_BYTES"),
        "measurement_error": result.stderr[:300] or None,
        "collector_returncode": result.returncode,
    }
print(
    json.dumps(
        {
            "scope": "Process/container lifetime counters and non-atomic current /data usage; not fault-window deltas. Missing shell/files are measurement gaps, not zeros.",
            "roles": rows,
        },
        indent=2,
    )
)
