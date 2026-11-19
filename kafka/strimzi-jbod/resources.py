"""Sample native CRI cumulative CPU and memory for every owned kind node."""

import gzip
import json
import os
import threading
import time

import cluster as lab


class Recorder:
    def __init__(self, root):
        self.path = root / "resources.jsonl.gz"
        self.nodes = lab.owner()["nodes"]
        self.stop_event = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, name="cri-resource-recorder")

    def start(self):
        self.thread.start()

    def run(self):
        try:
            with self.path.open("xb") as descriptor:
                with gzip.GzipFile(
                    fileobj=descriptor, mode="wb", compresslevel=1
                ) as stream:
                    while True:
                        started = time.monotonic()
                        for name, identity in self.nodes.items():
                            stats = json.loads(
                                lab.call(
                                    [
                                        "docker",
                                        "exec",
                                        identity,
                                        "crictl",
                                        "stats",
                                        "-o",
                                        "json",
                                    ],
                                    timeout=20,
                                )
                            )
                            stream.write(
                                (
                                    json.dumps(
                                        {
                                            "time": time.time(),
                                            "node": name,
                                            "stats": stats,
                                        }
                                    )
                                    + "\n"
                                ).encode()
                            )
                        stream.flush()
                        descriptor.flush()
                        os.fsync(descriptor.fileno())
                        if self.stop_event.wait(
                            max(0, 5 - (time.monotonic() - started))
                        ):
                            break
                descriptor.flush()
                os.fsync(descriptor.fileno())
        except Exception as error:
            self.error = error

    def check(self):
        if self.error:
            raise RuntimeError("CRI resource recorder failed") from self.error

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=100)
        if self.thread.is_alive():
            raise RuntimeError("resource recorder did not stop")
        self.check()


def summarize(path):
    """CRI counters are cumulative cgroup CPU; sample maxima are not true peaks."""
    first = {}
    last = {}
    peaks = {}
    samples = 0
    unavailable_cpu = {}
    first_time = None
    last_time = None
    with gzip.open(path, "rt") as stream:
        for line in stream:
            event = json.loads(line)
            first_time = event["time"] if first_time is None else first_time
            last_time = event["time"]
            initial_node = event["node"] not in first
            initial_ids = set()
            for stat in event["stats"]["stats"]:
                attributes = stat["attributes"]
                identity = attributes["id"]
                labels = attributes.get("labels", {})
                counter = (
                    stat.get("cpu", {}).get("usageCoreNanoSeconds", {}).get("value")
                )
                if counter is None:
                    unavailable_cpu[identity] = unavailable_cpu.get(identity, 0) + 1
                    continue
                cpu = int(counter)
                memory = stat.get("memory", {})
                row = last.setdefault(
                    identity,
                    {
                        "node": event["node"],
                        "namespace": labels.get("io.kubernetes.pod.namespace"),
                        "pod": labels.get("io.kubernetes.pod.name"),
                        "container": attributes["metadata"]["name"],
                        "initial_cpu_ns": cpu if initial_node else 0,
                        "first_sample_time": event["time"],
                    },
                )
                if cpu < row.get("last_cpu_ns", 0):
                    raise ValueError(
                        "native cumulative CPU decreased for same container ID"
                    )
                row["last_cpu_ns"] = cpu
                for key in ["rssBytes", "usageBytes", "workingSetBytes"]:
                    value = int(memory.get(key, {}).get("value", 0))
                    row["sampled_max_" + key] = max(
                        row.get("sampled_max_" + key, 0), value
                    )
                initial_ids.add(identity)
            if initial_node:
                first[event["node"]] = initial_ids
            samples += 1
    for identity, row in last.items():
        row["observed_cpu_core_seconds"] = (
            row["last_cpu_ns"] - row["initial_cpu_ns"]
        ) / 1e9
        peaks[identity] = row
    return {
        "node_samples": samples,
        "samples_without_cpu_by_container": unavailable_cpu,
        "first_time": first_time,
        "last_time": last_time,
        "cadence_target_seconds": 5,
        "containers": peaks,
        "observed_total_cpu_core_seconds": sum(
            r["observed_cpu_core_seconds"] for r in peaks.values()
        ),
        "limitations": "CRI cumulative CPU across all cgroup threads; sampled RSS/charged/working-set maxima. CRI may return only filesystem stats for stopped containers; these are counted as unavailable CPU samples, never zero CPU. Short-lived work and CPU after the final sample can be missed; host controllers and Docker VM overhead are excluded. Startup-existing containers subtract their first observed CPU; later containers start from zero.",
    }
