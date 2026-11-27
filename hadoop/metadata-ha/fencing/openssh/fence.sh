#!/usr/bin/env bash
set -euo pipefail
# Hadoop exports the peer being demoted as source_* (target_* for older callers).
host=${source_host:-${target_host:-}}
port=${source_port:-${target_port:-}}
[[ "$host" == nn1 || "$host" == nn2 ]]
[[ "$port" == 9000 ]]
# Bound the whole transport, not just connection establishment. Failure is closed.
timeout --signal=TERM --kill-after=2s 12s ssh -F /dev/null -p 2222 \
  -i /lab-ssh/id_ecdsa -o BatchMode=yes -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/lab-ssh/known_hosts \
  -o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2 \
  "hadoop@$host" 'python3 -' <<'PY'
import errno
import json
import socket
import subprocess
import time

result = subprocess.run(['fuser', '-k', '-n', 'tcp', '9000'], capture_output=True, text=True, timeout=3)
if result.returncode not in (0, 1):
    raise RuntimeError(result.stderr)
# Verify from the same host and configured listener, not a generic failed route.
import os
host = os.environ['HDFS_NAMENODE_ID'] if 'HDFS_NAMENODE_ID' in os.environ else socket.gethostname()
deadline = time.monotonic() + 2
while True:
    try:
        connection = socket.create_connection((host, 9000), timeout=0.5)
    except OSError as error:
        if error.errno == errno.ECONNREFUSED:
            break
        raise
    else:
        connection.close()
    if time.monotonic() >= deadline:
        raise RuntimeError('NameNode RPC listener survived fencing')
    time.sleep(0.05)
print('OPENSSH_FENCE ' + json.dumps({'host': host, 'port': 9000, 'pids': result.stdout.split(), 'listener': 'refused'}), flush=True)
PY
