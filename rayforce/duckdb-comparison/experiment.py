"""Serial, correctness-gated embedded analytics comparison with cgroup telemetry."""

import argparse
from collections import defaultdict
import csv
from datetime import datetime
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import random
import resource
import socket
import statistics
import subprocess
import time
import uuid
import urllib.parse

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
IMAGE = "rayforce-duckdb-analytics:dev"
PROJECT = "rayforce-duckdb-comparison"


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=60, **kwargs)


def capture(args, **kwargs):
    return subprocess.check_output(args, timeout=30, **kwargs)


def workload_image_exists():
    # A transport/permission error is unknown state, never an absent image.
    return (
        IMAGE
        in capture(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], text=True
        ).splitlines()
    )


def integer(name, default, low, high):
    value = int(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")
    return value


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def serialized(rows):
    return "".join(",".join(map(str, row)) + ";" for row in rows)


def prepare():
    rows = integer("ROWS", 2000000, 10000, 10000000)
    data = RESULTS / "data"
    if data.exists():
        manifest = json.loads((data / "manifest.json").read_text())
        if manifest["rows"] != rows:
            raise RuntimeError(
                "existing fixture has different ROWS; archive results before preparing another"
            )
        for name, expected in manifest["sha256"].items():
            with (data / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise RuntimeError("fixture hash mismatch: " + name)
        print("Retained fixture verified", flush=True)
        return manifest
    data.mkdir(parents=True)
    rng = random.Random(20261117)
    totals = [0, 0, 0]
    filtered = [0, 0, 0]
    symbols, customers, regions = (defaultdict(lambda: [0, 0]) for _ in range(3))
    started = time.monotonic()
    with (data / "trades.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["id", "customer", "symbol", "day", "qty", "price"])
        for i in range(rows):
            customer, symbol = rng.randrange(10000), rng.randrange(256)
            day, qty, price = (
                rng.randrange(30),
                rng.randrange(1, 101),
                rng.randrange(100, 10001),
            )
            writer.writerow([i, customer, symbol, day, qty, price])
            notional = qty * price
            for total in [totals] + ([filtered] if price >= 9000 and qty <= 5 else []):
                total[0] += 1
                total[1] += qty
                total[2] += notional
            for groups, key, value in [
                (symbols, symbol, notional),
                (customers, customer, notional),
                (regions, customer % 16, notional * (1 + customer % 7)),
            ]:
                groups[key][0] += 1
                groups[key][1] += value
    with (data / "customers.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["customer", "region", "factor"])
        writer.writerows((i, i % 16, 1 + i % 7) for i in range(10000))
    expected = {"totals": serialized([totals]), "filtered": serialized([filtered])}
    expected.update(
        {
            name: serialized([[key, *values] for key, values in sorted(groups.items())])
            for name, groups in [
                ("symbols", symbols),
                ("customers", customers),
                ("join", regions),
            ]
        }
    )
    save(data / "expected.json", expected)
    manifest = {
        "rows": rows,
        "customers": 10000,
        "seed": 20261117,
        "seconds": time.monotonic() - started,
        "sha256": {},
    }
    for name in ["trades.csv", "customers.csv", "expected.json"]:
        with (data / name).open("rb") as stream:
            manifest["sha256"][name] = hashlib.file_digest(stream, "sha256").hexdigest()
    save(data / "manifest.json", manifest)
    print(json.dumps(manifest), flush=True)
    return manifest


def queries(engine):
    if engine == "duckdb":
        trade_columns = ",".join(
            f"'{name}':'BIGINT'"
            for name in ["id", "customer", "symbol", "day", "qty", "price"]
        )
        dim_columns = "'customer':'BIGINT','region':'BIGINT','factor':'BIGINT'"
        loads = [
            f"CREATE TABLE trades AS SELECT * FROM read_csv('/data/trades.csv',header=true,columns={{{trade_columns}}})",
            f"CREATE TABLE customers AS SELECT * FROM read_csv('/data/customers.csv',header=true,columns={{{dim_columns}}})",
        ]
        q = {
            "totals": "SELECT count(id),sum(qty),sum(qty*price) FROM trades",
            "filtered": "SELECT count(id),sum(qty),sum(qty*price) FROM trades WHERE price>=9000 AND qty<=5",
            "symbols": "SELECT symbol,count(id),sum(qty*price) FROM trades GROUP BY symbol ORDER BY symbol",
            "customers": "SELECT customer,count(id),sum(qty*price) FROM trades GROUP BY customer ORDER BY customer",
            "join": "SELECT region,count(id),sum(qty*price*factor) FROM trades JOIN customers USING(customer) GROUP BY region ORDER BY region",
        }
    else:
        loads = [
            '(set trades (.csv.read [I64 I64 I64 I64 I64 I64] "/data/trades.csv"))',
            '(set customers (.csv.read [I64 I64 I64] "/data/customers.csv"))',
        ]
        q = {
            "totals": "(select {from: trades n: (count id) quantity: (sum qty) notional: (sum (* qty price))})",
            "filtered": "(select {from: trades where: (and (>= price 9000) (<= qty 5)) n: (count id) quantity: (sum qty) notional: (sum (* qty price))})",
            "symbols": "(select {from: trades by: symbol n: (count id) notional: (sum (* qty price)) asc: symbol})",
            "customers": "(select {from: trades by: customer n: (count id) notional: (sum (* qty price)) asc: customer})",
            "join": "(select {from: (inner-join [customer] trades customers) by: region n: (count id) notional: (sum (* (* qty price) factor)) asc: region})",
        }
    return loads, q


def plan(engine, target, repeats):
    loads, q = queries(engine)
    expected = json.loads((RESULTS / "data/expected.json").read_text())
    with target.open("w") as stream:
        for index, query in enumerate(loads):
            stream.write(f"load\tload{index}\t\t{query}\n")
        for phase, count in [("first", 1), ("warmup", 3), ("warm", repeats)]:
            for _ in range(count):
                for name, query in q.items():
                    stream.write(f"{phase}\t{name}\t{expected[name]}\t{query}\n")


def runtime_script(language, engine, filename, threads):
    if language == "python":
        args = [
            "python",
            "/workload/python/main.py",
            engine,
            "/plans/" + filename,
            str(threads),
        ]
    else:
        args = [f"/opt/bench/{language}_{engine}", "/plans/" + filename, str(threads)]
    # cgroup files remain in the container until collected; the exit marker keeps
    # a fast workload observable without sampling away its peak or CPU totals.
    import shlex

    return (
        "read before idle < /proc/uptime; echo __START_UPTIME__=$before; "
        + " ".join(map(shlex.quote, args))
        + "; code=$?; "
        "read after idle < /proc/uptime; echo __END_UPTIME__=$after; "
        "echo __EXIT__=$code; cat /sys/fs/cgroup/cpu.stat; "
        "echo __MEMORY_PEAK__; cat /sys/fs/cgroup/memory.peak; "
        "echo __MEMORY_EVENTS__; cat /sys/fs/cgroup/memory.events; "
        "echo __IO_STAT__; if [ -r /sys/fs/cgroup/io.stat ]; then "
        "cat /sys/fs/cgroup/io.stat; else echo unavailable; fi; "
        "echo __END_METRICS__; exit $code"
    )


def io_counters(text, seconds):
    """Kernel io.stat is device-keyed; absent accounting is not zero I/O."""
    if seconds <= 0:
        raise ValueError("non-positive workload observation window")
    if text.strip() == "unavailable":
        return {"available": False, "reason": "io.stat was not exposed"}
    devices = {}
    required = {"rbytes", "wbytes", "rios", "wios"}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        device = fields[0]
        major, minor = device.split(":")
        if not major.isdigit() or not minor.isdigit() or device in devices:
            raise ValueError("invalid or duplicate io.stat device")
        pairs = [s.split("=", 1) for s in fields[1:]]
        raw = dict(pairs)
        if len(raw) != len(pairs):
            raise ValueError("duplicate io.stat counter")
        # Linux 6.12 prints the device alone when all four counters are zero.
        if not required.intersection(raw):
            raw.update(dict.fromkeys(required, "0"))
        if not required <= raw.keys():
            raise ValueError("incomplete io.stat counters")
        values = {key: int(raw[key]) for key in required}
        if any(value < 0 for value in values.values()):
            raise ValueError("incomplete or negative io.stat counters")
        devices[device] = values
    totals = {
        key: sum(row[key] for row in devices.values()) for key in sorted(required)
    }
    return {
        "available": True,
        "devices": devices,
        "raw": text,
        "totals": totals,
        "per_second": {key: value / seconds for key, value in totals.items()},
        "scope": "Whole-container kernel block-I/O accounting, including wrapper. Empty counters/cached reads do not establish zero physical host I/O; Docker Desktop file sharing can perform work outside this cgroup. Not logical engine bytes, device latency or queue depth.",
    }


def container_seconds(inspection):
    state = inspection["State"]
    start, end = (
        datetime.fromisoformat(state[key].replace("Z", "+00:00"))
        for key in ("StartedAt", "FinishedAt")
    )
    seconds = (end - start).total_seconds()
    if seconds <= 0 or state["Running"]:
        raise ValueError("container lifetime is incomplete")
    return seconds


class DockerHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=10)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def docker_socket():
    endpoint = (
        os.environ.get("DOCKER_HOST")
        or json.loads(capture(["docker", "context", "inspect"], text=True))[0][
            "Endpoints"
        ]["docker"]["Host"]
    )
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "unix":
        raise RuntimeError(
            "resource sampling requires the active local Unix Docker API"
        )
    return urllib.parse.unquote(parsed.path)


def resource_sample(path, identity):
    connection = DockerHTTP(path)
    try:
        connection.request(
            "GET", "/containers/" + identity + "/stats?stream=false&one-shot=true"
        )
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError("Docker resource sampling HTTP " + str(response.status))
        stats = json.load(response)
        if not stats.get("memory_stats"):
            return None  # It may have exited between inspection and this snapshot.
        memory = stats["memory_stats"]
        return {
            "time": time.monotonic(),
            "cpu_ns": stats["cpu_stats"]["cpu_usage"]["total_usage"],
            "charged_memory_bytes": memory["usage"],
            "anonymous_bytes": memory.get("stats", {}).get("anon"),
            "file_cache_bytes": memory.get("stats", {}).get("file"),
            "kernel_memory_bytes": memory.get("stats", {}).get("kernel"),
        }
    finally:
        connection.close()


def resource_summary(samples):
    if not samples:
        return {
            "available": False,
            "reason": "workload completed before the first resource snapshot",
        }
    cpu = []
    for before, after in zip(samples, samples[1:]):
        elapsed = after["time"] - before["time"]
        delta = after["cpu_ns"] - before["cpu_ns"]
        if elapsed <= 0 or delta < 0:
            raise ValueError("resource timestamps/CPU counters moved backward")
        cpu.append(delta / 1e9 / elapsed * 100)
    return {
        "available": True,
        "sample_count": len(samples),
        "sampled_mean_charged_memory_bytes": statistics.mean(
            s["charged_memory_bytes"] for s in samples
        ),
        "sampled_max_charged_memory_bytes": max(
            s["charged_memory_bytes"] for s in samples
        ),
        "sampled_peak_cpu_percent_one_core_100": max(cpu) if cpu else None,
        "scope": "Approximately 100 ms plus API/inspection overhead; arithmetic sample mean, not heap or time-weighted mean. Sampling can miss short peaks; use native cgroup memory.peak and process RSS too. Docker daemon/VM overhead excluded.",
    }


def run_case(language, engine, trial, run, repeats, threads):
    name = f"rayforce-duckdb-{run.name[:8]}-{language}-{engine}-{trial}"
    filename = f"{engine}-{run.name}.tsv"
    path = RESULTS / "plans" / filename
    if not path.exists():
        plan(engine, path, repeats)
    shell = runtime_script(language, engine, filename, threads)
    cpus = float(os.environ.get("CPUS", str(threads)))
    if not 1 <= cpus <= 4:
        raise ValueError("CPUS must be 1..4")
    args = [
        "docker",
        "compose",
        "run",
        "--detach",
        "--no-deps",
        "--name",
        name,
        "--entrypoint",
        "/bin/sh",
        "analytics",
        "-c",
        shell,
    ]
    command(args, stdout=subprocess.PIPE, env=os.environ | {"CPUS": str(cpus)})
    started = time.monotonic()
    samples = []
    inspection = None
    endpoint = None
    try:
        while True:
            inspection = json.loads(capture(["docker", "inspect", name], text=True))[0]
            if (
                inspection["Config"]["Labels"].get("com.docker.compose.project")
                != PROJECT
            ):
                raise RuntimeError("unexpected container owner")
            if not inspection["State"]["Running"]:
                break
            endpoint = endpoint or docker_socket()
            stats = resource_sample(endpoint, inspection["Id"])
            if stats is not None:
                samples.append(stats)
            if time.monotonic() - started > 600:
                raise TimeoutError("workload exceeded 600 seconds")
            time.sleep(0.1)
        log = capture(["docker", "logs", name], text=True, stderr=subprocess.STDOUT)
        (run / (name + ".log")).write_text(log)
        save(run / (name + "-inspect.json"), inspection)
        save(run / (name + "-samples.json"), samples)
        if inspection["State"]["ExitCode"] or inspection["State"]["OOMKilled"]:
            raise RuntimeError(f"{name} failed: {log[-3000:]}")
        records = []
        for line in log.splitlines():
            fields = line.split("\t")
            if len(fields) == 5:
                phase, query, elapsed, cpu, rss = fields
                records.append(
                    {
                        "phase": phase,
                        "query": query,
                        "elapsed_ns": int(elapsed),
                        "cpu_ns": int(cpu),
                        "peak_rss_kib": int(rss),
                    }
                )
        expected_count = 2 + 5 * (4 + repeats)
        if len(records) != expected_count:
            raise RuntimeError("incomplete operation evidence")
        peak = int(log.split("__MEMORY_PEAK__\n")[1].splitlines()[0])
        cpu = int(
            next(
                line.split()[1]
                for line in log.splitlines()
                if line.startswith("usage_usec ")
            )
        )
        window = {}
        for label in ("START", "END"):
            prefix = "__" + label + "_UPTIME__="
            window[label] = float(
                next(
                    line[len(prefix) :]
                    for line in log.splitlines()
                    if line.startswith(prefix)
                )
            )
        seconds = container_seconds(inspection)
        io = io_counters(
            log.split("__IO_STAT__\n")[1].split("__END_METRICS__\n")[0], seconds
        )
        result = {
            "language": language,
            "engine": engine,
            "trial": trial,
            "records": records,
            "cgroup_peak_memory_bytes": peak,
            "cgroup_cpu_seconds": cpu / 1e6,
            "workload_window_seconds": window["END"] - window["START"],
            "container_lifetime_seconds": seconds,
            "cpu_utilization_percent_one_core_100": cpu / 1e6 / seconds * 100,
            "cpu_utilization_scope": "Cgroup CPU includes runtime, wrapper and accounting commands; denominator is Docker StartedAt to FinishedAt, parsed at microsecond resolution. CPU snapshot precedes the final accounting commands/exit, so their final CPU is not included. Driver uptime window has 10 ms resolution; per-query CPU and wall time are separate exact-clock samples.",
            "block_io": io,
            "sampled_resources": resource_summary(samples),
            "process_peak_rss_kib": max(r["peak_rss_kib"] for r in records),
            "image": inspection["Image"],
            "cpus": inspection["HostConfig"]["NanoCpus"] / 1e9,
            "memory_limit_bytes": inspection["HostConfig"]["Memory"],
            "wall_with_monitor_seconds": time.monotonic() - started,
            "memory_events": log.split("__MEMORY_EVENTS__\n")[1].split("__IO_STAT__\n")[
                0
            ],
        }
        save(run / (name + "-result.json"), result)
        return result
    except Exception as error:
        failure = {
            "error": str(error),
            "last_inspection": inspection,
            "samples": samples,
        }
        try:
            log = capture(["docker", "logs", name], text=True, stderr=subprocess.STDOUT)
            (run / (name + ".log")).write_text(log)
        except (subprocess.SubprocessError, OSError) as diagnostic_error:
            failure["log_error"] = str(diagnostic_error)
        save(run / (name + "-failure.json"), failure)
        raise
    finally:
        if (
            inspection is not None
            and inspection["Config"]["Labels"].get("com.docker.compose.project")
            == PROJECT
        ):
            command(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL)


def summarize(results, run):
    summary = []
    for language in ["python", "cpp", "rust"]:
        for engine in ["rayforce", "duckdb"]:
            cases = [
                r
                for r in results
                if r["language"] == language and r["engine"] == engine
            ]
            for query in queries(engine)[1]:
                per_trial = []
                for case in cases:
                    times = sorted(
                        r["elapsed_ns"] / 1e6
                        for r in case["records"]
                        if r["phase"] == "warm" and r["query"] == query
                    )
                    cpu = [
                        r["cpu_ns"] / 1e6
                        for r in case["records"]
                        if r["phase"] == "warm" and r["query"] == query
                    ]
                    per_trial.append(
                        {
                            "trial": case["trial"],
                            "p50_ms": statistics.median(times),
                            "p99_ms": times[math.ceil(len(times) * 0.99) - 1],
                            "mean_cpu_ms": statistics.mean(cpu),
                        }
                    )
                summary.append(
                    {
                        "language": language,
                        "engine": engine,
                        "query": query,
                        "median_trial_p50_ms": statistics.median(
                            c["p50_ms"] for c in per_trial
                        ),
                        "trials": per_trial,
                    }
                )
    save(run / "summary.json", summary)
    for row in summary:
        print(
            f"{row['language']:6} {row['engine']:8} {row['query']:10} {row['median_trial_p50_ms']:.3f} ms",
            flush=True,
        )


def negative_controls(run, threads):
    """Every language must reject a wrong oracle, an engine error and a null cell."""
    import shlex

    for engine in ["rayforce", "duckdb"]:
        sources = (
            ["SELECT 1::BIGINT", "SELECT missing FROM absent", "SELECT NULL::BIGINT"]
            if engine == "duckdb"
            else [
                "(table [value] (list [1]))",
                "(select {from: absent value: missing})",
                "(table [value] (list [0Nl]))",
            ]
        )
        filenames = []
        for index, source in enumerate(sources):
            filename = f"negative-{run.name}-{engine}-{index}.tsv"
            (RESULTS / "plans" / filename).write_text(
                f"first\tnegative{index}\t2;\t{source}\n"
            )
            filenames.append(filename)
        for language in ["python", "cpp", "rust"]:
            parts = []
            for filename in filenames:
                args = (
                    ["python", "/workload/python/main.py", engine]
                    if language == "python"
                    else [f"/opt/bench/{language}_{engine}"]
                ) + ["/plans/" + filename, str(threads)]
                parts.append(
                    " ".join(map(shlex.quote, args))
                    + '; code=$?; [ "$code" -eq 1 ] || exit 1'
                )
            script = "; ".join(parts) + "; echo CONTROLS_PASSED=3"
            completed = subprocess.run(
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "/bin/sh",
                    "analytics",
                    "-c",
                    script,
                ],
                text=True,
                capture_output=True,
                timeout=60,
            )
            log = completed.stdout + completed.stderr
            (run / f"controls-{language}-{engine}.log").write_text(log)
            if completed.returncode or "CONTROLS_PASSED=3" not in log:
                raise RuntimeError(
                    f"negative controls failed: {language}/{engine}: {log}"
                )
    save(
        run / "negative-controls.json",
        {
            "passed": 18,
            "languages": 3,
            "engines": 2,
            "cases": ["wrong expected result", "engine query error", "null result"],
        },
    )


def experiment(benchmark):
    controller_before = resource.getrusage(resource.RUSAGE_SELF)
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    controller_started = time.monotonic()
    fixture = prepare()
    threads = integer("THREADS", 1, 1, 4)
    repeats = integer("REPEATS", 15, 2, 1000) if benchmark else 2
    trials = integer("TRIALS", 3, 1, 10) if benchmark else 1
    (RESULTS / "plans").mkdir(exist_ok=True)
    run = RESULTS / "runs" / uuid.uuid4().hex
    run.mkdir(parents=True)
    save(
        run / "setup.json",
        {
            "fixture": fixture,
            "threads": threads,
            "repeats": repeats,
            "trials": trials,
            "source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(ROOT.rglob("*"))
                if p.is_file()
                and not any(
                    x in {"results", ".build", "__pycache__", "target"}
                    for x in p.relative_to(ROOT).parts
                )
            },
            "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "docker_info": json.loads(
                capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "sources": json.loads((ROOT / "sources.json").read_text()),
            "storage": {
                "input_csv_logical_bytes": sum(
                    (RESULTS / "data" / name).stat().st_size
                    for name in ("trades.csv", "customers.csv")
                ),
                "input_csv_host_allocated_bytes": sum(
                    (RESULTS / "data" / name).stat().st_blocks * 512
                    for name in ("trades.csv", "customers.csv")
                ),
                "engine_persistent_bytes": 0,
                "temporary_storage": "512 MiB tmpfs, charged to container memory",
                "native_compression_ratio": None,
                "limits": "This profile opens both engines in memory and mounts the same uncompressed CSV read-only. Host st_blocks*512 is allocated input storage, not native engine size or Docker VM disk use. No persisted-engine compression comparison or cold-storage claim.",
            },
        },
    )
    if not benchmark:
        negative_controls(run, threads)
    pairs = [
        (language, engine)
        for language in ["python", "cpp", "rust"]
        for engine in ["rayforce", "duckdb"]
    ]
    results = []
    for trial in range(trials):
        # Reverse pair order on alternate trials to reduce fixed-order thermal/cache bias.
        for language, engine in pairs[:: (-1 if trial % 2 else 1)]:
            print("Running", language, engine, "trial", trial + 1, flush=True)
            results.append(run_case(language, engine, trial + 1, run, repeats, threads))
    summarize(results, run)
    controller = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    save(
        run / "controller-resources.json",
        {
            "wall_seconds": time.monotonic() - controller_started,
            "user_cpu_seconds": controller.ru_utime - controller_before.ru_utime,
            "system_cpu_seconds": controller.ru_stime - controller_before.ru_stime,
            "child_cli_user_cpu_seconds": children.ru_utime - children_before.ru_utime,
            "child_cli_system_cpu_seconds": children.ru_stime
            - children_before.ru_stime,
            "process_peak_rss_bytes": controller.ru_maxrss
            * (1 if os.uname().sysname == "Darwin" else 1024),
            "scope": "Host controller includes retained-fixture hashing, native API sampling and report generation. CLI subprocess CPU excludes server-side work. Docker daemon/VM excluded; this is separate from engine container costs.",
        },
    )
    print("Evidence:", run, flush=True)


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["prepare", "test", "benchmark", "down", "docker-down"]
    )
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action in {"test", "benchmark"}:
        experiment(args.action == "benchmark")
    elif args.action == "down":
        active = capture(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
            ],
            text=True,
        ).strip()
        if active:
            raise RuntimeError(
                "owned workloads still exist; inspect them before cleanup"
            )
        print("No resident application; all evidence retained")
    else:
        remaining = capture(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
            ],
            text=True,
        ).strip()
        if remaining:
            raise RuntimeError("owned workloads remain; inspect them before teardown")
        command(
            [
                "docker",
                "compose",
                "--profile",
                "tools",
                "down",
                "--volumes",
                "--remove-orphans",
            ]
        )
        if workload_image_exists():
            command(["docker", "image", "rm", IMAGE])


if __name__ == "__main__":
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
