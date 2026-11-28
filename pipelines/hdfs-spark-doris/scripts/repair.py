"""Observe stock HDFS pending-reconstruction expiry without changing data/settings."""

import datetime
import json
import time
import lab
from topology import published_replication_restored


def observe(path):
    cases = sorted((lab.ROOT / "results").glob("datanode-failure-*"))
    case = cases[-1]
    if (
        json.loads((case / "fixture.json").read_text())["id"]
        != json.loads((path / "fixture.json").read_text())["id"]
    ):
        raise ValueError("Foreign failure fixture")
    fault = json.loads((case / "fault.json").read_text())
    killed = datetime.datetime.fromisoformat(
        fault["killed"]["FinishedAt"].replace("Z", "+00:00")
    ).timestamp()
    deadline = killed + 660
    script = "import urllib.request;print(urllib.request.urlopen('http://namenode:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState',timeout=5).read().decode())"
    samples = []
    while True:
        raw = lab.compose("exec", "-T", "namenode", "python3", "-c", script, timeout=15)
        row = json.loads(raw)["beans"][0]
        sample = {
            key: row.get(key)
            for key in (
                "LowRedundancyBlocks",
                "PendingReconstructionBlocks",
                "NumLiveDataNodes",
                "NumDeadDataNodes",
            )
        }
        sample["observed_ns"] = time.time_ns()
        samples.append(sample)
        lab.save(path / "repair-samples.json", samples)
        if (
            sample["PendingReconstructionBlocks"] == 0
            and sample["LowRedundancyBlocks"] == 0
            and sample["NumLiveDataNodes"] == 3
            and published_replication_restored(path)
        ):
            lab.save(
                path / "automatic-repair.json",
                dict(
                    passed=True,
                    failed_case=case.name,
                    original_180s_gate="failed and preserved",
                    fault_to_first_observed_full_rf3_s=time.time() - killed,
                    settings_changed=False,
                    data_reseeded=False,
                    samples=samples,
                ),
            )
            return
        if time.time() > deadline:
            break
        time.sleep(5)
    raise RuntimeError(
        "Stock automatic repair not observed within 660s of the original fault"
    )
