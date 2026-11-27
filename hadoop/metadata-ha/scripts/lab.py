"""CLI and role observations scoped to this example's Compose project."""

import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--project-name", "hadoop-metadata-ha"]


def command(*args, check=True, timeout=120):
    result = subprocess.run(
        [*COMPOSE, *args], cwd=ROOT, text=True, capture_output=True, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(f"Compose {args}: {result.stderr}\n{result.stdout}")
    return result


def execute(service, *args, user="hadoop", check=True, timeout=120):
    return command(
        "exec", "-T", "--user", user, service, *args, check=check, timeout=timeout
    )


def states():
    code = """import json,urllib.request
result={}
for node in ('nn1','nn2'):
    try:
        url='http://'+node+':9870/jmx?qry=Hadoop:service=NameNode,name=NameNodeStatus'
        value=json.load(urllib.request.urlopen(url,timeout=2))['beans'][0]
        result[node]=value
    except Exception as error:
        result[node]={'State':'unavailable','error':str(error)}
print(json.dumps(result))"""
    return json.loads(execute("tools", "python3", "-c", code).stdout)


def wait_roles(timeout=120, expected_active=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = states()
        active = [
            node for node, state in observed.items() if state.get("State") == "active"
        ]
        standby = [
            node for node, state in observed.items() if state.get("State") == "standby"
        ]
        if len(active) > 1:
            raise RuntimeError("Multiple active NameNodes observed")
        if (
            len(active) == 1
            and (expected_active is None or active == [expected_active])
            and len(standby) == 1
        ):
            return observed
        time.sleep(1)
    raise TimeoutError("Expected one active and one standby: " + str(observed))
