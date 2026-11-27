"""Curate executed operator findings without inventing benchmark qualification."""

import datetime
import json
import re
import statistics

import kube


def read(path):
    return json.loads(path.read_text())


def main():
    root = kube.ROOT / "results"
    native = max(
        (
            p
            for p in root.glob("native-*")
            if p.is_dir() and len(list(p.glob("*-effective-config.json"))) == 3
        ),
        key=lambda p: p.stat().st_mtime,
    )
    recoveries = {
        mode: read(
            max(
                root.glob(mode + "-recovery-*/result.json"),
                key=lambda p: p.stat().st_mtime,
            )
        )
        for mode in ("backend", "operator")
    }
    assert all(r["passed"] for r in recoveries.values())
    verifications = []
    for path in root.glob("verify-*/receipt.json"):
        result = read(path)
        bodies = [
            json.loads(line)
            for line in (path.parent / "job.log").read_text().splitlines()
            if line.startswith("{")
        ]
        assert len(bodies) == 1
        result = {
            key: result[key]
            for key in ("mode", "revision", "replica_index", "elapsed_s")
        }
        result["body_check"] = bodies[0]
        verifications.append(result)
    for revision in (1, 2):
        assert {
            r["replica_index"] for r in verifications if r["revision"] == revision
        } == {0, 1, 2}
    topology = read(
        max(root.glob("native-topology-*.json"), key=lambda p: p.stat().st_mtime)
    )
    settings = {}
    storage = {}
    cgroups = {}
    for path in native.glob("*-effective-config.json"):
        config = {r[0]: r[2] for r in read(path)}
        assert config["num_cores"] == "1" and config["mem_limit"] == "2G"
        settings[path.name.removesuffix("-effective-config.json")] = {
            k: config[k] for k in ("num_cores", "mem_limit")
        }
    for path in native.glob("*-disk.txt"):
        lines = path.read_text().splitlines()
        storage[path.name.removesuffix("-disk.txt")] = {
            "logical_directory_bytes": int(lines[0].split()[0]),
            "allocated_directory_bytes": int(lines[1].split()[0]) * 1024,
        }
    for path in native.glob("*-cgroup.txt"):
        text = path.read_text()
        cgroups[path.name.removesuffix("-cgroup.txt")] = {
            "current_container_cpu_core_seconds": int(
                re.search(r"^usage_usec (\d+)", text, re.M)[1]
            )
            / 1e6,
            "current_container_native_charged_memory_peak_bytes": int(
                re.search(r"^memory.peak\n(\d+)", text, re.M)[1]
            ),
            "current_container_lifetime_io": (
                {
                    k: sum(map(int, re.findall(r"\b" + k + r"=(\d+)", text)))
                    for k in ("rbytes", "wbytes", "rios", "wios")
                }
                if re.search(r"\brbytes=", text)
                else None
            ),
        }
    histories = []
    for path in root.glob("resources-*.jsonl"):
        actors = {}
        for line in path.read_text().splitlines():
            row = json.loads(line)
            for node in row["nodes"]:
                for pod in node.get("pods", []):
                    uid = pod["podRef"]["uid"]
                    stats = actors.setdefault(
                        uid, {"pod": pod["podRef"]["name"], "samples": []}
                    )
                    stamp = pod.get("cpu", {}).get("time")
                    native_time = (
                        datetime.datetime.fromisoformat(
                            stamp.replace("Z", "+00:00")
                        ).timestamp()
                        if stamp
                        else None
                    )
                    stats["samples"].append(
                        (
                            native_time,
                            pod.get("cpu", {}).get("usageCoreNanoSeconds"),
                            pod.get("memory", {}).get("workingSetBytes"),
                            pod.get("memory", {}).get("rssBytes"),
                        )
                    )
        summaries = []
        for uid, actor in actors.items():
            points = actor.pop("samples")
            rates = []
            cpu = 0
            resets = 0
            suppressed = 0
            for a, b in zip(points, points[1:]):
                if (
                    a[0] is None
                    or b[0] is None
                    or a[1] is None
                    or b[1] is None
                    or b[0] - a[0] < 0.05
                ):
                    suppressed += 1
                    continue
                if b[1] < a[1]:
                    resets += 1
                    continue
                cpu += b[1] - a[1]
                rates.append((b[1] - a[1]) / (b[0] - a[0]) / 1e7)
            summaries.append(
                {
                    **actor,
                    "pod_uid": uid,
                    "samples": len(points),
                    "observed_cpu_core_seconds_lower_bound": cpu / 1e9,
                    "sampled_cpu_mean_pct_one_core": (
                        statistics.mean(rates) if rates else None
                    ),
                    "sampled_cpu_peak_pct_one_core": max(rates, default=None),
                    "sampled_working_set_max_bytes": max(
                        (p[2] for p in points if p[2] is not None), default=None
                    ),
                    "sampled_rss_max_bytes": max(
                        (p[3] for p in points if p[3] is not None), default=None
                    ),
                    "observed_cpu_counter_resets": resets,
                    "suppressed_stale_or_invalid_cpu_intervals": suppressed,
                }
            )
        histories.append(
            {"history": str(path.relative_to(kube.ROOT)), "actors": summaries}
        )
    docker = read(root / "docker-environment.json")
    environment = {
        k: docker.get(k)
        for k in (
            "NCPU",
            "MemTotal",
            "KernelVersion",
            "Architecture",
            "OperatingSystem",
            "Driver",
            "ServerVersion",
        )
    }
    environment.update(
        {
            "disk_class": "Docker Desktop VM overlay; underlying device unmeasured",
            "co_tenancy": "Only this example lab active; kind workers share one VM",
        }
    )
    report = {
        "environment": environment,
        "tested_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "qualification": "Initial quiesced-data correctness and recovery diagnostics; no performance comparison or production-readiness qualification",
        "pins": read(kube.ROOT / "config/upstream.json"),
        "versions": read(native / "versions.json"),
        "native_engine_versions": sorted({f["Version"] for f in topology["frontends"]}),
        "mysql_compatibility_version": (native / "version.tsv").read_text(),
        "effective_backend_settings": settings,
        "native_topology": topology,
        "full_body_verifications": verifications,
        "recoveries": recoveries,
        "native_storage_directories": storage,
        "native_current_container_cgroups": cgroups,
        "resource_histories": histories,
        "show_data_raw": (native / "data.tsv").read_text(),
        "show_create_table_raw": (native / "table.tsv").read_text(),
        "compression_ratio": {
            "value": None,
            "reason": "Native table stats are asynchronous and no matching native uncompressed/compressed payload counters were qualified; directory bytes include metadata and obsolete versions.",
        },
        "kernel_limits": {p.name: p.read_text() for p in native.glob("*-kernel.txt")},
        "source_inventory": read(kube.ROOT / ".build/source-inventory.json"),
        "failed_setup_findings": [
            "Initial kind all-platform import referenced missing foreign-platform content; explicit native-platform import succeeded.",
            "Pod-ready BEs initially returned No available backends; failed setup Job retained. A bounded native capacity gate succeeded without resetting storage or seeding early.",
        ],
        "limitations": [
            "All workers share one Docker VM; no independent host/power-loss or backup qualification.",
            "Eight hash tablets are triplicated on three BEs; capacity scale-out and tenant-exclusive placement are unqualified.",
            "Fresh Go Jobs do not clear native or shared VM caches; verified cold-read performance remains unrun.",
            "Finite bulk SQL commands each have one ACK; no external per-row witness or continuous writer during faults.",
            "CPU/Pod memory samples and current-container cgroup peaks differ; startup/exit gaps and counter resets make observed totals lower bounds.",
            "Network raw counters are saved but not aggregated; block I/O service latency, complete short-lived Go CPU/memory and whole build/import resource costs are incomplete.",
            "SHOW DATA can lag successful exact reads; it must not replace the independent row oracle or be treated as a compression measurement.",
            "Existing VM max_map_count is 262144; no privileged host sysctl changes. The small fixture does not qualify upstream production kernel sizing.",
            "Retained repeated setup, scaling, FE/whole-node/disk/quorum loss, partitions, upgrades, pressure, independent restore and fair comparative performance remain unrun.",
        ],
    }
    (kube.ROOT / "reports/INITIAL_RESULTS.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
