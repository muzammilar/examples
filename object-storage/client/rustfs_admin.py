"""Use botocore's SigV4 signer for RustFS's native admin API, not homemade auth."""

import json
import os
from workload import save
import time
from urllib.request import Request, urlopen
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials


def post(token=None):
    url = os.environ["S3_ENDPOINT"] + "/rustfs/admin/v3/heal/lab-objects"
    if token:
        url += "?clientToken=" + token
    data = (
        json.dumps(
            {
                "recursive": True,
                "dryRun": False,
                "remove": False,
                "recreate": False,
                "scanMode": 2,
                "updateParity": False,
                "nolock": False,
                "readRepair": False,
            }
        ).encode()
        if not token
        else b""
    )
    r = AWSRequest(
        method="POST", url=url, data=data, headers={"Content-Type": "application/json"}
    )
    credentials = Credentials(
        os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"]
    )
    S3SigV4Auth(credentials, "s3", "us-east-1").add_auth(r)
    with urlopen(
        Request(url, data=data, headers=dict(r.headers.items()), method="POST"),
        timeout=120,
    ) as response:
        return json.load(response)


started = post()
save("heal-start", started)
print(started, flush=True)
token = started["clientToken"]
start = time.monotonic()
while time.monotonic() - start < 900:
    result = post(token)
    save("heal-status", result)
    outcome = result.get("outcome", {})
    execution = outcome.get("execution", {})
    print(
        json.dumps(
            {
                "summary": result.get("summary"),
                "execution": execution,
                "coverage": outcome.get("coverage"),
            }
        ),
        flush=True,
    )
    if execution.get("state") == "completed":
        counters = outcome.get("counters", {})
        if any(
            counters.get(k, -1) != 0 for k in ["unknown", "failed", "skipped"]
        ) or counters.get("overflowed", True):
            raise RuntimeError("heal lacks verified coverage: " + str(counters))
        if outcome.get("coverage") != "complete":
            raise RuntimeError("heal completed with partial coverage")
        break
    if execution.get("state") in [
        "failed",
        "completed_with_errors",
        "aborted",
        "cancelled",
    ]:
        raise RuntimeError("native heal failed: " + json.dumps(result))
    time.sleep(5)
else:
    raise RuntimeError("native heal timeout")
