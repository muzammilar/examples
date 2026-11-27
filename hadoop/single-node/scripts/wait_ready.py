import json
import time
import urllib.error
import urllib.request

deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(
            "http://namenode:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState",
            timeout=3,
        ) as response:
            state = json.load(response)["beans"][0]
        if state["NumLiveDataNodes"] == 1:
            print(json.dumps(state))
            break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("One live DataNode did not register")
