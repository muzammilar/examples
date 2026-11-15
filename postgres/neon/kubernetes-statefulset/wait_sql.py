"""Wait for authenticated SQL through the same Service path as the workload."""

import os
from pathlib import Path
import subprocess
import sys
import time

key = sys.argv[1]
if key not in ("NEON_DSN", "BRANCH_DSN"):
    raise ValueError("expected NEON_DSN or BRANCH_DSN")
os.environ["KUBECONFIG"] = str(Path("results/kubeconfig").resolve())
command = [
    "kubectl",
    "--context",
    "kind-neon-storage",
    "-n",
    "neon-lab",
    "exec",
    "client",
    "--",
    "python",
    "-c",
    "import os, psycopg; db=psycopg.connect(os.environ["
    + repr(key)
    + "], connect_timeout=3); assert db.execute('SELECT 1').fetchone()==(1,); db.close()",
]
end = time.monotonic() + 180
while time.monotonic() < end:
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode == 0:
        print(key, "SQL ready through Service")
        break
    time.sleep(1)
else:
    raise RuntimeError("SQL readiness timeout: " + result.stderr[-4000:])
