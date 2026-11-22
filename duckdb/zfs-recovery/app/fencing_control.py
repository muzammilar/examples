"""Prove an SSH timeout fences only the exact owned remote export helper."""

import json
import shlex
import subprocess
import uuid

import archive_verify
import history
import lab

root = lab.RESULTS / ("native-fencing-control-" + uuid.uuid4().hex)
root.mkdir()
token = uuid.uuid4().hex
helper = "/tmp/ducklab-archive-control-" + token + ".py"
output = "/tmp/ducklab-fence-heartbeat-" + token
body = """import os,sys,time
with open(sys.argv[3], 'x') as stream:
 while True:
  stream.write(str(time.time_ns())+'\\n');stream.flush();os.fsync(stream.fileno());time.sleep(.1)
"""
lab.ssh(
    "primary",
    shlex.join(
        [
            "/opt/venv/bin/python",
            "-c",
            "from pathlib import Path;Path("
            + repr(helper)
            + ").write_text("
            + repr(body)
            + ")",
        ]
    ),
)
owner = lab.ssh("primary", "systemctl show ducklab.service -p MainPID --value")
arguments = [
    "/opt/venv/bin/python",
    helper,
    "export",
    "unused-in-memory-control",
    output,
]
try:
    archive_verify.native_job(root, token, arguments, timeout=10)
except subprocess.TimeoutExpired:
    pass
else:
    raise RuntimeError("the deliberately running helper did not time out")
evidence = json.loads((root / ("native-failure-" + token + "-export.json")).read_text())
if "fencing_error" in evidence or len(evidence["fencing"]["termination"]) != 1:
    raise RuntimeError("the timeout did not prove termination of a running helper")
if archive_verify.native_processes("primary")["helpers"]:
    raise RuntimeError("a native helper survived the timeout control")
if lab.ssh("primary", "systemctl show ducklab.service -p MainPID --value") != owner:
    raise RuntimeError("the unrelated database owner changed during fencing")
heartbeat = lab.ssh("primary", "cat " + output)
result = {
    "passed": True,
    "owner_pid_unchanged": owner,
    "fencing": evidence["fencing"],
    "heartbeat_before_fencing": heartbeat,
    "scope": "Deliberate SSH timeout against a generated heartbeat helper; no system-under-test SQL or database files are modified.",
}
history.save(root / "result.json", result)
print(json.dumps(result))
