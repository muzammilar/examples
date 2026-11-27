"""Selected native guest, daemon and storage counters; no credentials exported."""

import json
from pathlib import Path
import shlex
from guest import ssh

GUEST = r"""
import json, pathlib, subprocess, time
def call(*args):
    return subprocess.check_output(args, text=True).strip()
units = call('systemctl', 'list-units', '--type=service', '--all', '--no-legend', '--plain').splitlines()
result = {'kernel': call('uname', '-r'), 'memory': pathlib.Path('/proc/meminfo').read_text(),
          'diskstats': pathlib.Path('/proc/diskstats').read_text(), 'units': {},
          'sample_monotonic_ns': time.monotonic_ns(),
          'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
for line in units:
    name = line.split()[0]
    if not name.startswith(('ceph-mon@','ceph-mgr@','ceph-mds@','ceph-osd@','cephfs-')):
        continue
    raw = call('systemctl','show',name,'-p','ControlGroup','-p','CPUUsageNSec','-p','MemoryCurrent','-p','MemoryPeak','-p','IOReadBytes','-p','IOWriteBytes')
    row = dict(part.split('=',1) for part in raw.splitlines())
    group = pathlib.Path('/sys/fs/cgroup') / row['ControlGroup'].lstrip('/')
    for key in ('cpu.stat','memory.current','memory.peak','io.stat'):
        path = group / key
        row[key] = path.read_text() if path.is_file() else None
    result['units'][name] = row
print(json.dumps(result))
"""


def main():
    result = {"guests": {}}
    for node in range(3):
        result["guests"][str(node)] = json.loads(
            ssh(node, "python3 -c " + shlex.quote(GUEST), True)
        )
    for name, command in {
        "df": "ceph df detail -f json",
        "status": "ceph status -f json",
        "osd_perf": "ceph osd perf -f json",
        "data_pool_config": "ceph osd pool get labfs_data all -f json",
        "metadata_pool_config": "ceph osd pool get labfs_meta all -f json",
        "filesystem": "ceph fs status -f json",
        "effective_mds_cache": "ceph config get mds mds_cache_memory_limit -f json",
    }.items():
        result[name] = json.loads(ssh(0, command, True))
    result["virtual_disks"] = [
        {
            "path": str(p.relative_to("/state")),
            "apparent_bytes": p.stat().st_size,
            "allocated_bytes": p.stat().st_blocks * 512,
        }
        for p in sorted(Path("/state").rglob("*.qcow2"))
    ]
    print(json.dumps(result))


if __name__ == "__main__":
    main()
