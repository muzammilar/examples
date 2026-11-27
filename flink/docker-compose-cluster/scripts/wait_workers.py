"""Verify the native Flink worker registry before submitting a job."""
import json
import time
import urllib.error
import urllib.request


deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen("http://jobmanager:8081/taskmanagers", timeout=3) as response:
            state = json.load(response)
        workers = state["taskmanagers"]
        if len(workers) == 2 and sum(worker["slotsNumber"] for worker in workers) == 2:
            print(json.dumps(state))
            break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Two native one-slot Flink workers did not register")
