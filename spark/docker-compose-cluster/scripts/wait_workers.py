"""Wait for both workers in the master's native registry."""
import json
import time
import urllib.error
import urllib.request


deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen("http://master:8080/json/", timeout=3) as response:
            state = json.load(response)
        workers = [worker for worker in state["workers"] if worker["state"] == "ALIVE"]
        if len(workers) == 2 and sum(worker["cores"] for worker in workers) == 2:
            print(json.dumps(state))
            break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Two registered one-core Spark workers did not become ready")
