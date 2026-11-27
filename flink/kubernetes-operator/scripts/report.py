"""Summarize observed native outcomes and resource bounds without performance ranking."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load(name):
    return json.loads((RESULTS / name).read_text())


def receipt(log):
    for line in (RESULTS / log).read_text().splitlines():
        if line.startswith('{"mode":'):
            return json.loads(line)
    raise ValueError(f"No native receipt in {log}")


def summarize(history):
    rows = [json.loads(line) for line in (RESULTS / history).read_text().splitlines()]
    actors = {}
    nodes = set()
    images = set()
    latest_counts = None
    for row in rows:
        if "checkpoints" in row:
            latest_counts = row["checkpoints"]["counts"]
        for pod in row["pods"]["items"]:
            if pod["metadata"].get("labels", {}).get("component") == "taskmanager":
                if "nodeName" in pod["spec"]:
                    nodes.add(pod["spec"]["nodeName"])
            for status in pod.get("status", {}).get("containerStatuses", []):
                if status.get("imageID"):
                    images.add(status["imageID"])
        for name, sample in row["cgroups"].items():
            if sample["code"] != 0:
                continue
            raw = sample["out"]
            usage = re.search(r"^usage_usec (\d+)$", raw, re.M)
            current = re.search(r"^memory.current\n(\d+)$", raw, re.M)
            peak = re.search(r"^memory.peak\n(\d+)$", raw, re.M)
            if not usage or not current or not peak:
                continue
            actor = actors.setdefault(name, {"samples": [], "memory": [], "peak": []})
            values = {key: sum(map(int, re.findall(r"\b" + key + r"=(\d+)", raw)))
                      for key in ("rbytes", "wbytes", "rios", "wios")}
            values["cpu_usec"] = int(usage[1])
            actor["samples"].append((row["elapsed_s"], values))
            actor["memory"].append(int(current[1]))
            actor["peak"].append(int(peak[1]))
    summaries = []
    for name, actor in actors.items():
        samples = actor["samples"]
        span = samples[-1][0] - samples[0][0]
        deltas = {key: sum(max(0, new[key] - old[key]) for (_, old), (_, new) in zip(samples, samples[1:]))
                  for key in ("rbytes", "wbytes", "rios", "wios", "cpu_usec")}
        summaries.append({"pod": name, "samples": len(samples), "observation_s": span,
                          "average_cpu_pct_of_one_core_lower_bound": deltas["cpu_usec"] / 1e6 / span * 100 if span else None,
                          "sampled_memory_current_peak_bytes": max(actor["memory"]),
                          "native_cgroup_lifetime_memory_peak_bytes": max(actor["peak"]),
                          "observed_positive_deltas_lower_bound": deltas,
                          "read_bytes_per_s_lower_bound": deltas["rbytes"] / span if span else None,
                          "write_bytes_per_s_lower_bound": deltas["wbytes"] / span if span else None,
                          "read_iops_lower_bound": deltas["rios"] / span if span else None,
                          "write_iops_lower_bound": deltas["wios"] / span if span else None})
    return {"history": history, "last_observed_checkpoint_counts": latest_counts,
            "observed_taskmanager_workers": sorted(nodes), "container_image_ids": sorted(images),
            "resources": summaries}


def main():
    combined = load("task-recovery-receipt.json")
    clean = receipt("test-clean-capacity.log")
    logs = (RESULTS / "combined-recovery-manager.log").read_text()
    durations = [int(value) for value in re.findall(r"checkpointDuration=(\d+) ms", logs)]
    sources = ["sql/orders.sql", "scripts/objects.py", "scripts/job.py", "deploy/orders.yaml",
               "deploy/storage/base/resources.yaml", "config/kubectl.json"]
    report = {
        "date": "2026-11-26", "classification": "functional-and-recovery-diagnostics",
        "engine": "Flink 2.3.0", "operator": "1.16.1", "seaweedfs": "4.55",
        "docker_environment": load("docker-environment.json"),
        "current_client_server_versions": load("kubernetes-version-supported-client.json"),
        "clean_corrected_capacity": {"native_result": clean, "readback": load("verification.json"),
            "native_final_checkpoints": next(value["counts"] for key, value in load("clean-native-job.json").items() if key.endswith("/checkpoints")),
            "failed_checkpoint_cause": "Checkpoint triggered before all required tasks were running; native startup attempt rejected.",
            "storage_allocated_bytes": int((RESULTS / "clean-storage-final.txt").read_text().split()[0]) * 1024,
            "observations": summarize(clean["history"])},
        "combined_worker_and_storage_recovery": {"native_result": combined,
            "readback": load("combined-verification.json"),
            "capacity_failure": "Four 64 MiB SeaweedFS volume slots exhausted; master reported no writable/free volumes while TCP readiness remained true.",
            "recovery": "Increase volume.max from 4 to 32, restart only SeaweedFS on the same retained PVC. No Flink reset or input reload.",
            "longest_completed_checkpoint_ms": max(durations),
            "storage_allocated_bytes_after_recovery": int((RESULTS / "combined-storage-final.txt").read_text().split()[0]) * 1024,
            "observations": summarize(combined["history"])},
        "repeated_setup": load("repeat-setup-proof.json"),
        "deployed_source_matches": load("running-source-proof.json"),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        "limitations": [
            "No comparative performance benchmark or production-readiness qualification.",
            "Initial controls used host kubectl 1.34.1 against server 1.37.0, outside supported skew. Example-local 1.37.3 was then used for repeated setup, completion/readback and teardown.",
            "Combined run is worker Pod loss plus storage-capacity intervention; 327 seconds is not an isolated worker-failover RTO.",
            "Clean corrected-capacity run injected no fault; one startup checkpoint failed before tasks were ready.",
            "Initial Docker capacity attempt reached 99 MiB free; own cluster teardown and separately authorized unused-build-cache cleanup preceded successful retries.",
            "Resource deltas miss work outside sample windows; peak cgroup counters include container lifetime, not only the observation phase.",
            "Detailed network accounting, device latency/queue depth, controller/control-plane overhead and short-lived oracle CPU remain incomplete.",
            "CSV reference gzip is not native object-store compression. Allocated storage includes obsolete checkpoint chunks and metadata; compaction was not tested.",
            "One VM/failure domain, one non-HA S3 dependency; no external live ACK witness, scale/savepoint round trip, manager HA, independent restore or node/disk-loss qualification.",
            "Admission webhook disabled in initial profile. TCP Pod readiness does not detect exhausted writable volume slots; up additionally requires an exact S3 write/read/delete probe."
        ],
    }
    (ROOT / "reports/INITIAL_RESULTS.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": "reports/INITIAL_RESULTS.json", "clean_rows": report["clean_corrected_capacity"]["readback"]["orders"],
                      "combined_checkpoint_max_ms": max(durations)}))


if __name__ == "__main__":
    main()
