"""Publish a complete six-cell operational cohort; never hide failed histories."""

import datetime
import hashlib
import json
from pathlib import Path

import control
import resources


def compact_verification(value):
    return {
        k: v
        for k, v in value.items()
        if k not in {"observed_live_ids", "export_process"}
    }


def native_values(value):
    text = value["native_counters_disk_and_main_process"]
    disk = text.split("__DISK\n", 1)[1].split("__FILESYSTEM", 1)[0].splitlines()
    process_status = text.partition("__PROCESS\n")[2]
    process_memory = {"VmRSS": None, "VmHWM": None}
    for line in process_status.splitlines():
        fields = line.split()
        if fields and fields[0].rstrip(":") in process_memory:
            if len(fields) != 3 or fields[2] != "kB":
                raise RuntimeError("unexpected Linux process memory units")
            process_memory[fields[0].rstrip(":")] = int(fields[1]) * 1024
    return {
        "pid1_memory_bytes": process_memory,
        "pid1_status": process_status,
        "pid1_memory_scope": "Linux /proc/1/status snapshot and lifetime high-water RSS for PID 1 only. Both databases have additional processes: the live CedarDB audit found a much larger second cedardb process, and PostgreSQL has backend/worker processes. PID 1 RSS is not whole-engine RSS. Use cgroup charged memory for whole-database comparisons; RSS is not heap size.",
        "database_logical_bytes": int(disk[0].split()[0]),
        "database_allocated_bytes": int(disk[1].split()[0]),
        "cgroup_peak_memory_bytes_since_last_start": int(
            text.split("__memory.peak\n", 1)[1].splitlines()[0]
        ),
        "native_compression": value["native_compression"],
        "native_counter_text": text.split("__PROCESS", 1)[0],
        "image_id": value["container"]["Image"],
        "image_reference": value["container"]["Config"]["Image"],
        "cpus": value["container"]["HostConfig"]["NanoCpus"] / 1e9,
        "memory_limit_bytes": value["container"]["HostConfig"]["Memory"],
    }


def table(head, rows):
    return "\n".join(
        [
            "| " + " | ".join(head) + " |",
            "|" + "|".join("---" for _ in head) + "|",
            *["| " + " | ".join(map(str, row)) + " |" for row in rows],
        ]
    )


def publish():
    lab = json.loads((control.RESULTS / "lab.json").read_text())
    if lab["phase"] != "ready":
        raise RuntimeError("fixture is not fully initialized")
    candidates = {}
    for path in (control.RESULTS / "histories").glob("*/meta.json"):
        meta = json.loads(path.read_text())
        if meta.get("generation", "initial") != lab["generation"]:
            continue
        if (path.parent / "failure.json").exists():
            raise RuntimeError("qualification generation contains a failed case")
        if meta["action"] not in {"graceful", "crash", "backup"}:
            continue
        key = (meta["backend"], meta["action"])
        candidates.setdefault(key, []).append((meta["created_ns"], path.parent))
    expected = {
        (name, action)
        for name in control.BACKENDS
        for action in ("graceful", "crash", "backup")
    }
    if set(candidates) != expected:
        raise RuntimeError("operational cohort is incomplete")
    cases = []
    selected_started = []
    for key in sorted(expected):
        started, root = max(candidates[key])
        selected_started.append(started)
        result = json.loads((root / "result.json").read_text())
        if not result["passed"] or result["verification"]["missing_required_acks"]:
            raise RuntimeError("case did not pass complete readback")
        before, after = (
            json.loads((root / (name + ".json")).read_text())
            for name in ("before", "after")
        )
        native = {name: native_values(value) for name, value in after.items()}
        if any(
            value["cpus"] != 4 or value["memory_limit_bytes"] != 6 * 1024**3
            for value in native.values()
        ):
            raise RuntimeError("database budgets differ")
        actors = {}
        totals = {}
        for role in ("writer", "reader"):
            actor = json.loads((root / (role + "-inspect.json")).read_text())
            if (
                actor["HostConfig"]["NanoCpus"] != 1000000000
                or actor["HostConfig"]["Memory"] != 512 * 1024**2
            ):
                raise RuntimeError("workload budgets differ")
            actors[role] = {
                "image_id": actor["Image"],
                "cpus": 1,
                "memory_limit_bytes": 512 * 1024**2,
                "process_resources": [
                    json.loads(line)
                    for line in (root / (role + ".log")).read_text().splitlines()
                    if line.startswith("{")
                ],
            }
            events = control.journal(root / (role + ".jsonl"))
            for phase in json.loads((root / "phases.json").read_text()):
                for kind, metrics in result["phases"][role][phase["name"]].items():
                    values = [
                        e["elapsed_ns"] / 1e6
                        for e in events
                        if e["kind"] == kind
                        and phase["started_ns"] <= e["started_ns"] < phase["ended_ns"]
                    ]
                    metrics["mean_ms"] = sum(values) / len(values) if values else None
            totals[role] = {
                kind: sum(e["kind"] == kind for e in events)
                for kind in ("intent", "ok", "fail", "ambiguous", "mismatch")
            }
            if totals[role]["mismatch"]:
                raise RuntimeError("successful response violated its body contract")
        raw = [
            json.loads(line)
            for line in (root / "resources.jsonl").read_text().splitlines()
        ]
        phase_resources = {
            phase: resources.summarize_records([r for r in raw if r["phase"] == phase])
            for phase in sorted({r["phase"] for r in raw})
        }
        case = {
            "backend": key[0],
            "action": key[1],
            "history": root.name,
            "verification": compact_verification(result["verification"]),
            "operation_totals": totals,
            "phases": result["phases"],
            "longest_success_gap_seconds": result["longest_success_gap_seconds"],
            "restore_summary": result.get("restore_summary"),
            "actors": actors,
            "native_after": native,
            "native_before": {
                name: native_values(value) for name, value in before.items()
            },
            "resources": resources.summarize(root / "resources.jsonl"),
            "phase_resources": phase_resources,
        }
        if key[1] == "backup":
            restored = json.loads((root / "restored-data.json").read_text())
            if (
                restored["missing_required_acks"]
                or not (root / "restore-write-rejected.json").exists()
            ):
                raise RuntimeError("restore/read-only proof is incomplete")
            case["restored"] = compact_verification(restored)
            case["restored_native"] = native_values(
                json.loads((root / "restored-native.json").read_text())
            )
            case["backup_client"] = json.loads(
                (root / "native-backup.json").read_text()
            )
            case["maintenance"] = [
                json.loads(p.read_text())
                for p in sorted(root.glob("maintenance-*.json"))
            ]
            if not case["maintenance"]:
                raise RuntimeError("backup lacks measured maintenance utility costs")
            for tool in case["maintenance"]:
                for record in tool["records"]:
                    if record.get("kind") == "process-resources":
                        quota, period = map(int, record["cpu.max"].split())
                        if (
                            quota != period
                            or int(record["memory.max"]) != 512 * 1024**2
                        ):
                            raise RuntimeError(
                                "maintenance budget did not match 1 CPU / 512 MiB"
                            )
        cases.append(case)
    folder = control.RESULTS / "generations" / lab["generation"]
    setup = {
        name: native_values(value)
        for name, value in json.loads((folder / "after-load.json").read_text()).items()
    }
    headers = [
        json.loads(p.read_text()) for p in control.RESULTS.glob("environment-*.json")
    ]
    header = max(
        (h for h in headers if h["created_ns"] <= min(selected_started)),
        key=lambda h: h["created_ns"],
    )
    for name in (
        "control.py",
        "resources.py",
        "docker-compose.yml",
        "orders-workload/main.go",
        "orders-workload/process_test.go",
        "orders-workload/go.mod",
        "orders-workload/go.sum",
        "orders-workload/Dockerfile",
    ):
        if (
            hashlib.sha256((control.ROOT / name).read_bytes()).hexdigest()
            != header["source_sha256"][name]
        ):
            raise RuntimeError(
                "runtime source changed after the selected environment header: " + name
            )
    if (
        len({actor["image_id"] for case in cases for actor in case["actors"].values()})
        != 1
    ):
        raise RuntimeError("selected operational cells used different workload images")
    output = {
        "format_version": 1,
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "generation": lab["generation"],
        "hardware_audit": json.loads(
            (control.RESULTS / "hardware-audit.json").read_text()
        ),
        "post_case_storage_file_audit": json.loads(
            (control.RESULTS / "storage-file-audit.json").read_text()
        ),
        "dataset": {
            "seed_rows_per_backend": lab["rows"],
            "customers": 10000,
            "payload_bytes": 256,
            "shape": "id I64 PK, customer/amount I32, TEXT; 192-byte repeated customer prefix + 64 ASCII SHA256 hex bytes; no nulls",
        },
        "versions": header["versions"],
        "environment": {
            key: header[key]
            for key in ("host", "disk_class", "topology", "client_rate")
        },
        "docker_environment": {
            key: header["docker_info"][key]
            for key in (
                "Architecture",
                "NCPU",
                "MemTotal",
                "KernelVersion",
                "ServerVersion",
            )
        },
        "contract": json.loads((control.ROOT / "contract.json").read_text()),
        "cases": cases,
        "additional_passed_histories_in_fixture": sum(
            len(values) for values in candidates.values()
        )
        - len(cases),
        "setup_native": setup,
        "setup_resources": resources.summarize(folder / "setup-resources.jsonl"),
        "setup_postprocessor_recovery": json.loads(
            (folder / "summary-recovery.json").read_text()
        )
        if (folder / "summary-recovery.json").exists()
        else None,
        "development_outcomes": json.loads(
            (control.RESULTS / "development-outcomes.json").read_text()
        ),
        "controller_costs": [
            json.loads(p.read_text())
            for p in (control.RESULTS / "controller-costs").glob("*.json")
        ],
        "encoded_seed_csv_bytes_reconstructed": sum(
            len(str(i))
            + len(str(i % 10000))
            + len(str((i % 100000) * 17 % 100000))
            + 260
            for i in range(lab["rows"])
        ),
        "seed_byte_scope": "Exact deterministic CSV body encoding reconstructed independently; no header or COPY/TCP framing. Actual COPY stream bytes were not separately counted.",
        "controller_cost_scope": "All saved actions, including development and postprocessor recovery; not a sum restricted to the six selected cases.",
        "driver_controls": [
            json.loads(p.read_text())
            for p in (control.RESULTS / "driver-controls").glob("*.json")
            if json.loads(p.read_text())["generation"] == lab["generation"]
        ],
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "tested_runtime_source_sha256": {
            name: header["source_sha256"][name]
            for name in (
                "control.py",
                "resources.py",
                "docker-compose.yml",
                "orders-workload/main.go",
                "orders-workload/Dockerfile",
            )
        },
        "limits": "Single-host process/backup observations, not HA or capacity. CPU reset and partial intervals are excluded from observed deltas. Automatic native compression CPU is not isolated. Physical device latency/queue depth, daemon/VM overhead and controller average RSS are unavailable. Backup/clone implementation and permission models differ; no performance winner is established.",
    }
    if not output["driver_controls"] or not all(
        d["passed"] for d in output["driver_controls"]
    ):
        raise RuntimeError(
            "native duplicate/missing-key controls have not passed for this fixture"
        )
    (control.ROOT / "VERIFIED_RESULTS.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
    summary, latencies, costs, io_rows = [], [], [], []
    for case in cases:
        name, action = case["backend"], case["action"]
        totals = case["operation_totals"]
        summary.append(
            [
                name,
                action,
                case["verification"]["all_historical_acks"],
                totals["writer"]["fail"],
                totals["writer"]["ambiguous"],
                totals["reader"]["fail"],
                f"{case['longest_success_gap_seconds']['writer']:.3f} / {case['longest_success_gap_seconds']['reader']:.3f}",
            ]
        )
        for role in ("writer", "reader"):
            row = [name, action, role]
            for phase in ("baseline", "recovered"):
                metrics = case["phases"][role][phase]["ok"]
                row.append(
                    f"{metrics['count']}; {metrics['p50_ms']:.3f} / {metrics['p99_ms']:.3f}"
                )
            latencies.append(row)
        r = case["resources"][name]
        native = case["native_after"][name]
        memory = r["sampled_charged_memory_bytes_min_mean_max"]
        io_row = [name, action]
        for operation in ("read", "write"):
            values = [
                value
                for key, value in r["io_and_network"].items()
                if key.startswith("block_") and key.endswith("_" + operation + "_bytes")
            ]
            if len(values) == 1 and values[0]["observed_delta"] is not None:
                io_row.append(
                    f"{values[0]['observed_delta'] / 1048576:.3f} / {values[0]['per_second'] / 1048576:.3f}"
                )
            else:
                io_row.append("unavailable/multiple devices; see JSON")
        io_rows.append(io_row)
        costs.append(
            [
                name,
                action,
                f"{r['observed_cpu_core_seconds']:.3f}",
                f"{r['average_cpu_percent_one_core_100_observed_intervals']:.1f}%",
                f"{memory[1] / 1048576:.1f} / {memory[2] / 1048576:.1f}",
                f"{native['cgroup_peak_memory_bytes_since_last_start'] / 1048576:.1f}",
                f"{native['database_allocated_bytes'] / 1048576:.1f}",
            ]
        )
    text = [
        "## Measured operational results",
        f"**{output['date_utc'][:10]}**, generation `{lab['generation']}`: **{lab['rows']:,} seeded orders per backend**, 10,000 customers, 256-byte synthetic payloads. Six serial cases at equal **4 CPU / 6 GiB** database caps; independent **1 CPU / 512 MiB** actors, maximum 50 requests/s each. These are short operational observations, not capacity benchmarks or repeated-trial latency rankings.",
        f"The input stream contains **{output['encoded_seed_csv_bytes_reconstructed']:,} CSV bytes per backend**, reconstructed from the deterministic encoder (without header/protocol framing). Each row has 256 payload bytes; persisted size and allocated space are measured separately.",
        f"The fixture also preserves **{output['additional_passed_histories_in_fixture']} earlier successful histories** from qualification refinements. The table selects the newest case per backend/action; runtime source hashes, workload image and resource budgets match across those six cells.",
        "All six cases passed complete seeded/live body reconciliation and offline latest-read lower-bound checks. Graceful stops exited 0; SIGKILL targets exited 137 and restarted with retained volumes. The unrelated backend did not restart. Counts below include connection and shutdown errors; ACK totals are cumulative within each backend's fixture. Ambiguous writes remain separate from ACKs, including when their bodies appear after recovery.",
        table(
            [
                "Backend",
                "Case",
                "Historical live ACKs",
                "Write fail",
                "Write ambiguous",
                "Read fail",
                "Longest write/read success gap s",
            ],
            summary,
        ),
        "The restart cases deliberately hold the only instance down for **5 seconds**. Success gaps include that hold, pacing and reconnection; they are not leader-election time or independent-host failover. Successful and failed request latency remain separate in the JSON.",
        table(
            [
                "Backend",
                "Case",
                "Actor",
                "Baseline n; p50/p99 ms",
                "Recovered n; p50/p99 ms",
            ],
            latencies,
        ),
        "Request clocks bracket connection/configuration when needed, SQL and body validation. Writer intent fsync occurs before the request timer; final ACK journal sync is outside it. Phase rates include pacing/witness costs. Paired reads fetch one rotating seeded key and the newest key; these fit in memory and do not qualify cold storage.",
        table(
            [
                "Backend",
                "Case",
                "Observed CPU core-s",
                "Mean CPU (1 core=100%)",
                "Charged memory mean/max MiB",
                "Native peak since last start MiB",
                "DB allocated MiB",
            ],
            costs,
        ),
        table(
            [
                "Backend",
                "Case",
                "Observed read MiB / MiB/s",
                "Observed write MiB / MiB/s",
            ],
            io_rows,
        ),
        "Per-device kernel block counters, not logical query bytes or proof of physical host traffic. Docker Desktop sharing/cache can move I/O outside a container. Native io.stat operation counters remain in snapshots; Docker API IOPS/latency/queue fields are unavailable on this cgroup-v2 runtime rather than zero.",
        "CPU values cover observed counter intervals, **excluding reset/initial/final partial intervals**, not complete CPU totals across crashes. Native memory peaks are lifetime-since-start counters, not per-phase peaks; sampled means/maxima cover the case. Database bytes include catalog/index/WAL data. Shared backups are counted once in the disk series; restore copies, actor peaks, native client CPU/RSS, controller costs, per-device I/O bytes/rates and phase summaries are retained in [VERIFIED_RESULTS.json](VERIFIED_RESULTS.json). Raw samples/history remain ignored under `results/`.",
    ]
    for case in cases:
        if case["action"] == "backup":
            r = case["restore_summary"]
            text.append(
                f"**{case['backend']} restore:** {lab['rows']:,} baseline rows and **{r['required_pre_backup_acks']:,} pre-backup ACKs** verified, **0 required missing**. **{r['snapshot_excluded_acks_at_case_end']:,} later source ACKs** were outside the restored snapshot at case end: measured snapshot RPO, not source data loss. Writes to the restore session were rejected and its complete body remained valid. The archive is mounted read-only for cloning; the database opens a separate verified copy on fresh owned storage."
            )
    native_rows = [
        row
        for row in setup["cedar"]["native_compression"]["records"]
        if isinstance(row, list) and row[0] in {"id", "customer", "amount", "payload"}
    ]
    if native_rows:
        uncompressed = sum(int(row[3]) for row in native_rows)
        encoded = sum(int(row[4]) for row in native_rows)
        compressed = sum(int(row[5]) for row in native_rows)
        text.append(
            f"**Native CedarDB visible-column cold statistics after load:** {uncompressed:,} uncompressed / {encoded:,} encoded / {compressed:,} compressed bytes, combined **{uncompressed / compressed:.3f}×** at that layer; codecs/encodings and per-column values are in the JSON. Internal transaction columns are excluded. These are native reported bytes, not the CSV or complete database directory."
        )
    pg_rows = [
        row
        for row in setup["postgres"]["native_compression"]["records"]
        if isinstance(row, list)
    ]
    text.append(
        "**PostgreSQL payload storage after load:** "
        + "; ".join(
            f"{row[1]:,} cells, {row[2]:,} pg_column_size bytes, codec {row[0] or 'NULL (not compressed)'}"
            for row in pg_rows
        )
        + ". This includes value headers and is a different layer from CedarDB's cold-column statistics."
    )
    text.append(
        "A post-case file audit explains the larger CedarDB whole-store footprint: `db.ballast` occupied about **1 GiB**, two `db.wal.*` files about **256 MiB each**, and `db.0_0` about **49 MiB**. PostgreSQL's `base` directory was about **347 MiB** and `pg_wal` about **288 MiB**. These are allocated-file observations at audit time, not compression ratios or claims about the purpose/reclamation of undocumented files. Raw paths/bytes are retained in the JSON."
    )
    sample = cases[0]["verification"]
    text.extend(
        [
            f"One final verified source export in this cohort contained **{sample['csv_bytes']:,} bytes**; its streamed gzip level 1 output was **{sample['gzip1_bytes']:,} bytes**, ratio **{sample['gzip1_ratio']:.3f}×**. This is CSV compressibility, not engine compression. Native CedarDB cold-column codec/byte statistics and PostgreSQL payload compression/storage statistics are separately recorded; their layers and coverage differ. Gzip CPU/wall costs are timed without file/hash work; native automatic-compression CPU remains mixed into whole-container costs.",
            "Development evidence remains separate: the original remote-replication authentication failure stays failed; all source ACKs were independently reconciled afterward. Enterprise-only GRANT and the unrelated host-variable routing probe remain documented limitations/failures. Setup's null-IOPS postprocessor error was repaired from preserved raw samples without reloading data; its original failure is retained. Earlier development CLI CPU percentages used incompatible timing boundaries and are excluded from this qualification; final clients use matched main-window CPU/wall deltas.",
            "Remaining: disk loss/EIO/full-disk, partitions, pressure, upgrade/rollback, interrupted backup, schema/transaction anomaly workloads, sustained repeated benchmarks and genuine cold-cache behavior. A correctness/recovery pass does not establish complete production readiness.",
        ]
    )
    path = control.ROOT / "README.md"
    existing = path.read_text()
    start, end = "<!-- verified-results -->", "<!-- /verified-results -->"
    block = start + "\n\n" + "\n\n".join(text) + "\n\n" + end
    if start in existing:
        existing = existing.split(start)[0] + block + existing.split(end, 1)[1]
    else:
        existing += "\n\n" + block + "\n"
    path.write_text(existing)
    print("Published", len(cases), "operational cases", lab["generation"])


if __name__ == "__main__":
    publish()
