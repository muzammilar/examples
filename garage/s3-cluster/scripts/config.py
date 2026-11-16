import json
import os
import re
from pathlib import Path

out = Path(os.environ.get("GARAGE_CONFIG_DIR", "config/generated"))
out.mkdir(parents=True, exist_ok=True)
marker = out / "bootstrap.json"
peers = json.loads(marker.read_text())["peers"] if marker.exists() else []
if peers and (
    len(peers) != 9
    or len({p.split("@")[0] for p in peers}) != 9
    or any(
        not re.fullmatch(r"[0-9a-f]{64}@garage" + str(i) + r":3901", p)
        for i, p in enumerate(peers)
    )
):
    raise RuntimeError("bootstrap peer identities/addresses do not match this example")
for i in range(9):
    (out / f"garage{i}.toml").write_text(f'''metadata_dir = "/meta"
data_dir = "/data"
metadata_snapshots_dir = "/meta/snapshots"
db_engine = "sqlite"
replication_factor = 3
consistency_mode = "consistent"
metadata_fsync = true
data_fsync = true
metadata_auto_snapshot_interval = "1h"
compression_level = "none"
block_size = "1M"
block_ram_buffer_max = "64MiB"
rpc_secret = "{"a1" * 32}"
rpc_timeout_msec = 5000
rpc_bind_addr = "0.0.0.0:3901"
rpc_public_addr = "garage{i}:3901"
bootstrap_peers = {json.dumps([p for n, p in enumerate(peers) if n != i])}
[s3_api]
api_bind_addr = "0.0.0.0:3900"
s3_region = "us-east-1"
[admin]
api_bind_addr = "0.0.0.0:3903"
admin_token = "local-only-garage-admin-token"
''')
(out / "haproxy.cfg").write_text(
    """global
  maxconn 256
resolvers docker
  nameserver dns 127.0.0.11:53
  resolve_retries 3
  timeout resolve 1s
  timeout retry 1s
  hold valid 2s
  hold nx 2s
  hold timeout 2s
  hold refused 2s
  hold other 2s
defaults
  mode http
  timeout connect 3s
  timeout client 60s
  timeout server 60s
frontend s3
  bind :9000
  default_backend garage
frontend health
  bind :8404
  http-request return status 200 content-type text/plain string ready
backend garage
  balance roundrobin
"""
    + "".join(
        f"  server garage{i} garage{i}:3900 check resolvers docker resolve-prefer ipv4 init-addr libc,none inter 1s fall 2 rise 2\n"
        for i in range(9)
    )
)
Path(os.environ.get("RESULTS_DIR", "results/manual")).mkdir(parents=True, exist_ok=True)
