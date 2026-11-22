"""Host-only Compose control; no Docker socket is exposed to the workload."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid

sys.path.insert(0, str(Path(__file__).parent / "app"))
import history

ROOT = Path(__file__).resolve().parent
PROJECT = "duckdb-zfs-recovery"
COMPOSE = ["docker", "compose", "--profile", "tools"]


def run(command, timeout=60):
    return subprocess.run(
        command, cwd=ROOT, check=True, text=True, capture_output=True, timeout=timeout
    ).stdout.strip()


def inspect(name):
    value = json.loads(run(["docker", "inspect", name]))[0]
    labels = value["Config"].get("Labels", {})
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or Path(labels.get("com.docker.compose.project.working_dir", "")).resolve()
        != ROOT
    ):
        raise RuntimeError("container does not belong to this example")
    return value


def state(value):
    return {
        "id": value["Id"],
        "image": value["Image"],
        "state": value["State"],
        "cpus": value["HostConfig"]["NanoCpus"] / 1e9,
        "memory_limit_bytes": value["HostConfig"]["Memory"],
        "swap_limit_bytes": value["HostConfig"]["MemorySwap"],
    }


SAMPLE = """import json, pathlib, time
p=pathlib.Path('/sys/fs/cgroup'); out={'utc_ns':time.time_ns(),'monotonic_ns':time.monotonic_ns()}
for name in ('cpu.stat','cpu.max','memory.current','memory.peak','memory.events','memory.max','memory.swap.max','io.stat'):
 try:out[name]=(p/name).read_text()
 except OSError as e:out[name]={'unavailable':str(e)}
out['network']=pathlib.Path('/proc/net/dev').read_text()
out['process_started_ticks']=pathlib.Path('/proc/1/stat').read_text().split(') ',1)[1].split()[19]
out['storage']={}
for f in pathlib.Path('/state').glob('*'):
 if f.is_file():
  s=f.stat();out['storage'][f.name]={'logical_bytes':s.st_size,'allocated_bytes':s.st_blocks*512}
print(json.dumps(out))
"""


def wait_healthy(container, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = inspect(container)
        if not value["State"]["Running"]:
            raise RuntimeError("owned guest exited during restart")
        if value["State"].get("Health", {}).get("Status") == "healthy":
            return value
        time.sleep(1)
    raise RuntimeError("owned guest health deadline exceeded")


class Resources:
    def __init__(self, root, names):
        self.root, self.names = root, names
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.collect_checked)
        self.thread.start()

    def collect_checked(self):
        try:
            self.collect()
        except BaseException:
            self.error = traceback.format_exc()
            self.stop.set()

    def collect(self):
        with (self.root / "resources.jsonl").open("x") as stream:
            while not self.stop.is_set():
                for role, name in self.names.items():
                    started = time.monotonic_ns()
                    try:
                        if name == "@controllers":
                            candidates = [
                                line.split()[0]
                                for line in run(
                                    [
                                        "docker",
                                        "ps",
                                        "--filter",
                                        "label=com.docker.compose.project=" + PROJECT,
                                        "--format",
                                        '{{.ID}} {{.Label "com.docker.compose.service"}}',
                                    ]
                                ).splitlines()
                                if line.split()[-1] in ("controller", "live-controller")
                            ]
                            if len(candidates) != 1:
                                raise RuntimeError(
                                    "no single active example controller"
                                )
                            name = candidates[0]
                        value = json.loads(
                            run(
                                ["docker", "exec", name, "python3", "-c", SAMPLE],
                                timeout=8,
                            )
                        )
                    except (
                        subprocess.SubprocessError,
                        json.JSONDecodeError,
                        RuntimeError,
                    ) as error:
                        value = {"unavailable": str(error)}
                    history.emit(
                        stream,
                        {
                            "role": role,
                            "started_ns": started,
                            "ended_ns": time.monotonic_ns(),
                            "sample": value,
                        },
                    )
                self.stop.wait(1)

    def finish(self):
        self.stop.set()
        self.thread.join(30)
        if self.thread.is_alive():
            raise RuntimeError("resource collector did not stop")
        if self.error:
            raise RuntimeError("resource collector failed: " + self.error)


def resource_summary(root, records=None):
    records = history.events(root / "resources.jsonl") if records is None else records
    output = {}
    for role in ("primary", "standby", "controller"):
        samples = [e for e in records if e["role"] == role]
        valid = [e["sample"] for e in samples if "unavailable" not in e["sample"]]
        deltas, memory, resets = [], [], 0
        previous = None
        io_totals = {}
        io_seconds = {}
        for value in valid:
            cpu = dict(line.split() for line in value["cpu.stat"].splitlines())
            current = {
                "ns": value["monotonic_ns"],
                "cpu": int(cpu["usage_usec"]),
                "generation": value["process_started_ticks"],
                "user": int(cpu["user_usec"]),
                "system": int(cpu["system_usec"]),
            }
            memory.append(int(value["memory.current"]))
            io = {}
            for line in value["io.stat"].splitlines():
                device, *fields = line.split()
                io[device] = {
                    k: int(v) for field in fields for k, _, v in [field.partition("=")]
                }
                if not fields:
                    io[device] = {
                        key: 0 for key in ("rbytes", "wbytes", "rios", "wios")
                    }
            current["io"] = io
            if previous:
                seconds = (current["ns"] - previous["ns"]) / 1e9
                change = current["cpu"] - previous["cpu"]
                if change < 0 or current["generation"] != previous["generation"]:
                    resets += 1
                elif seconds > 0:
                    deltas.append(
                        (
                            change / 1e6,
                            seconds,
                            (current["user"] - previous["user"]) / 1e6,
                            (current["system"] - previous["system"]) / 1e6,
                        )
                    )
                    for device, counters in io.items():
                        if device not in previous["io"]:
                            continue
                        for field, count in counters.items():
                            delta = count - previous["io"][device].get(field, count)
                            if delta >= 0:
                                key = device + ":" + field
                                io_totals[key] = io_totals.get(key, 0) + delta
                                io_seconds[key] = io_seconds.get(key, 0) + seconds
            previous = current
        output[role] = {
            "samples": len(samples),
            "unavailable_samples": len(samples) - len(valid),
            "observed_cpu_core_seconds": sum(d[0] for d in deltas) if deltas else None,
            "observed_user_cpu_core_seconds": sum(d[2] for d in deltas)
            if deltas
            else None,
            "observed_system_cpu_core_seconds": sum(d[3] for d in deltas)
            if deltas
            else None,
            "observed_cpu_percent_one_core_100": sum(d[0] for d in deltas)
            / sum(d[1] for d in deltas)
            * 100
            if deltas
            else None,
            "peak_sampled_cpu_percent_one_core_100": max(
                (d[0] / d[1] * 100 for d in deltas), default=None
            ),
            "counter_resets_excluded": resets,
            "memory_mean_bytes": sum(memory) / len(memory) if memory else None,
            "memory_sampled_max_bytes": max(memory, default=None),
            "native_memory_peak_bytes": max(
                (int(v["memory.peak"]) for v in valid), default=None
            ),
            "block_io_by_device_counter": {
                key: {
                    "observed_delta": value,
                    "observed_seconds": io_seconds[key],
                    "per_second": value / io_seconds[key],
                }
                for key, value in io_totals.items()
            }
            or None,
            "storage_peak_logical_bytes": max(
                (sum(f["logical_bytes"] for f in v["storage"].values()) for v in valid),
                default=None,
            ),
            "storage_peak_allocated_bytes": max(
                (
                    sum(f["allocated_bytes"] for f in v["storage"].values())
                    for v in valid
                ),
                default=None,
            ),
        }
    output["scope"] = (
        "Native Docker cgroup counters including QEMU/guest work and charged file cache. Approximately1s plus sequential docker-exec overhead; actual sample timestamps retained. Reset/initial/final partial CPU/IO intervals excluded. Disk logical sparse capacity is different from allocated bytes. Per-device bytes/operation counters cannot be summed across stacked layers. Physical disk latency/queue depth unavailable. Network and per-phase raw samples retained. Functional co-tenancy is allowed; no capacity ranking."
    )
    return output


def source_hashes():
    paths = [
        *ROOT.glob("app/*.py"),
        ROOT / "production.py",
        ROOT / "Makefile",
        ROOT / "docker-compose.yml",
        ROOT / "vm/Dockerfile",
        ROOT / "vm/boot.py",
        ROOT / ".dockerignore",
        ROOT / "live-contract.json",
    ]
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def availability(root):
    phases = {
        p["name"]: p["monotonic_ns"] for p in history.events(root / "phases.jsonl")
    }
    result = {}
    for role, pattern in (("writer", "writer-*.jsonl"), ("reader", "reader.jsonl")):
        records = [
            e
            for path in root.glob(pattern)
            for e in history.events(path)
            if e["kind"] != "intent"
        ]
        successes = sorted(e["ended_ns"] for e in records if e["kind"] == "ok")
        first = min(
            (value for value in successes if value >= phases["native-recovery"]),
            default=None,
        )
        during_fault = [
            e
            for e in records
            if phases["fault"] <= e["started_ns"] < phases["native-recovery"]
        ]
        if (
            first is None
            or not during_fault
            or not any(e["kind"] in ("fail", "ambiguous") for e in during_fault)
        ):
            raise RuntimeError("missing live outage/recovery observations for " + role)
        result[role] = {
            "first_success_after_vm_restart_seconds_since_fault": (
                first - phases["fault"]
            )
            / 1e9,
            "longest_observed_success_completion_gap_seconds": max(
                (b - a for a, b in zip(successes, successes[1:])), default=0
            )
            / 1e9,
            "outage_attempts": len(during_fault),
            "outage_failed_or_ambiguous": sum(
                e["kind"] in ("fail", "ambiguous") for e in during_fault
            ),
        }
    result["scope"] = (
        "Operation completion gaps include pacing, request duration and client reconnection; not exact server downtime or election time. First success is after guest restart, while native service recovery is observed. Actor monotonic clocks share the Docker VM kernel, not the host or guest kernel."
    )
    return result


def crash():
    host_started = time.monotonic()
    host_cpu_before = resource.getrusage(resource.RUSAGE_SELF)
    child_cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    base = (ROOT / os.environ.get("RESULTS_DIR", "results/manual")).resolve()
    if not base.is_relative_to(ROOT / "results"):
        raise RuntimeError("results must remain inside this example's ignored results/")
    root = base / "live" / uuid.uuid4().hex
    root.mkdir(parents=True)
    lock = (ROOT / "results/production.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = {
        "table": "live_" + root.name,
        "rows": int(os.environ.get("ROWS", "2000000")),
        "customers": int(os.environ.get("CUSTOMERS", "10000")),
        "clients": int(os.environ.get("CLIENTS", "4")),
        "batch_rows": int(os.environ.get("LIVE_BATCH_ROWS", "1000")),
        "delay_seconds": float(os.environ.get("LIVE_WRITE_DELAY_S", "0.5")),
        "reader_delay_seconds": 0.2,
        "baseline_seconds": 10,
        "recovered_seconds": 10,
        "source_sha256": source_hashes(),
        "scope": "Retained-disk guest crash with beta Quack; functional correctness/recovery, not physical host power loss, independent-host HA or capacity comparison.",
    }
    if (
        config["rows"] < 1000
        or not 1 <= config["customers"] <= config["rows"]
        or not 1 <= config["clients"] <= 8
        or not 1 <= config["batch_rows"] <= 10000
        or not 0.1 <= config["delay_seconds"] <= 10
    ):
        raise ValueError("invalid bounded live workload configuration")
    history.save(root / "config.json", config)
    initial = {}
    names = {}
    for role in ("primary", "standby"):
        names[role] = run(COMPOSE + ["ps", "--all", "-q", role])
        initial[role] = state(inspect(names[role]))
        if (
            (role == "primary" and not initial[role]["state"]["Running"])
            or initial[role]["cpus"] != 2
            or initial[role]["memory_limit_bytes"] != 3 * 1024**3
        ):
            raise RuntimeError("expected running VM with original resource budget")
    # This profile does not replicate its table to standby. Stop that unused VM
    # before the recorded workload so independent functional labs fit the host.
    run(COMPOSE + ["stop", "standby"])
    initial["standby"] = state(inspect(names["standby"]))
    if initial["standby"]["state"]["Running"]:
        raise RuntimeError("unused standby did not stop")
    name = PROJECT + "-live-" + root.name
    names["controller"] = name
    history.save(
        root / "environment.json",
        {
            "host_system": platform.system(),
            "host_kernel": platform.release(),
            "host_architecture": platform.machine(),
            "docker": json.loads(run(["docker", "info", "--format", "{{json .}}"])),
            "vms": initial,
            "co_tenancy": run(["docker", "ps", "--format", "{{.Names}} {{.Image}}"]),
            "cpu_model": run(["sysctl", "-n", "machdep.cpu.brand_string"])
            if platform.system() == "Darwin"
            else platform.processor(),
        },
    )
    collector = Resources(
        root, {role: name for role, name in names.items() if role != "standby"}
    )
    container = None
    original_error = None
    try:
        container = run(
            COMPOSE
            + [
                "run",
                "-d",
                "--name",
                name,
                "live-controller",
                "/results/live/" + root.name,
            ]
        )
        history.save(root / "controller-started.json", state(inspect(container)))
        image_sources = json.loads(
            run(
                [
                    "docker",
                    "exec",
                    container,
                    "python3",
                    "-c",
                    "import hashlib,json,pathlib; p=pathlib.Path('/opt/lab/app'); print(json.dumps({'app/'+f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob('*.py')}))",
                ]
            )
        )
        if image_sources != {
            name: digest
            for name, digest in config["source_sha256"].items()
            if name.startswith("app/")
        }:
            raise RuntimeError(
                "workload image does not contain the tested application sources"
            )
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if collector.error:
                raise RuntimeError("resource collector failed during the run")
            current = inspect(container)
            if not current["State"]["Running"]:
                break
            if (root / "restart-request.json").exists() and not (
                root / "restart-complete.json"
            ).exists():
                dead = inspect(names["primary"])
                if dead["State"]["Running"]:
                    time.sleep(0.2)
                    continue
                if dead["State"]["Pid"] != 0:
                    raise RuntimeError("old QEMU process not fenced")
                history.save(root / "fenced-container.json", state(dead))
                time.sleep(5)
                run(["docker", "start", names["primary"]])
                recovered = state(wait_healthy(names["primary"]))
                survivor = state(inspect(names["standby"]))
                if (
                    recovered["state"]["StartedAt"]
                    == initial["primary"]["state"]["StartedAt"]
                    or survivor["state"]["StartedAt"]
                    != initial["standby"]["state"]["StartedAt"]
                    or survivor["state"]["Running"]
                ):
                    raise RuntimeError("recovery or survivor identity invalid")
                history.save(
                    root / "restart-complete.json",
                    {
                        "primary": recovered,
                        "standby": survivor,
                        "completed_ns": time.monotonic_ns(),
                    },
                )
            time.sleep(0.5)
        else:
            raise RuntimeError("continuous recovery controller deadline exceeded")
        history.save(root / "controller-stopped.json", state(current))
        with (root / "controller.log").open("x") as stream:
            subprocess.run(
                ["docker", "logs", container],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=30,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if current["State"]["ExitCode"] != 0 or not (root / "result.json").exists():
            raise RuntimeError(
                "recovery failed; retain VM volumes and all case evidence"
            )
        if source_hashes() != config["source_sha256"]:
            raise RuntimeError("source changed during experiment")
    except BaseException:
        original_error = traceback.format_exc()
        history.save(
            root / "host-failure.json",
            {"passed": False, "error": original_error},
        )
        raise
    finally:
        cleanup_errors = []
        try:
            if container and inspect(container)["State"]["Running"]:
                run(["docker", "stop", "-t", "30", container], timeout=45)
        except BaseException:
            cleanup_errors.append(traceback.format_exc())
        finally:
            try:
                collector.finish()
                history.save(root / "resource-summary.json", resource_summary(root))
                host_cpu = resource.getrusage(resource.RUSAGE_SELF)
                child_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
                history.save(
                    root / "host-controller-resources.json",
                    {
                        "wall_seconds": time.monotonic() - host_started,
                        "user_cpu_seconds": host_cpu.ru_utime
                        - host_cpu_before.ru_utime,
                        "system_cpu_seconds": host_cpu.ru_stime
                        - host_cpu_before.ru_stime,
                        "reaped_children_user_cpu_seconds": child_cpu.ru_utime
                        - child_cpu_before.ru_utime,
                        "reaped_children_system_cpu_seconds": child_cpu.ru_stime
                        - child_cpu_before.ru_stime,
                        "process_lifetime_peak_rss_bytes": host_cpu.ru_maxrss
                        * (1 if platform.system() == "Darwin" else 1024),
                        "scope": "Host orchestration and resource collection, including its own reaped Docker CLI children. Separate from Docker cgroups; do not add Docker CPU to the same process counters twice. RSS is process-lifetime peak, not heap or whole-host memory.",
                    },
                )
            except BaseException:
                cleanup_errors.append(traceback.format_exc())
            lock.close()
        if cleanup_errors:
            history.save(
                root / "cleanup-failure.json",
                {"errors": cleanup_errors, "original_error": original_error},
            )
            if original_error is None:
                raise RuntimeError(
                    "cleanup/resource finalization failed; see preserved diagnostics"
                )
    history.save(
        root / "host-result.json",
        {
            "passed": True,
            "source_sha256": config["source_sha256"],
            "result_sha256": hashlib.sha256(
                (root / "result.json").read_bytes()
            ).hexdigest(),
            "availability": availability(root),
        },
    )
    run(["docker", "rm", container])
    print(str(root.relative_to(ROOT)), flush=True)


def fixture_identity():
    values = json.loads(
        run(["docker", "volume", "inspect", PROJECT + "_primary", PROJECT + "_standby"])
    )
    identity = {}
    for value in values:
        labels = value.get("Labels") or {}
        role = labels.get("com.docker.compose.volume")
        if labels.get("com.docker.compose.project") != PROJECT or role not in (
            "primary",
            "standby",
        ):
            raise RuntimeError("unowned native fixture volume")
        identity[role] = {
            key: value[key] for key in ("Name", "CreatedAt", "Mountpoint")
        }
    if set(identity) != {"primary", "standby"}:
        raise RuntimeError("complete native fixture identity required")
    return identity


def registered_bases(identity):
    bases = set()
    for path in (ROOT / "results").glob("fixture-registration-*.json"):
        value = json.loads(path.read_text())
        if value["volumes"] != identity:
            continue
        base = (ROOT / value["results_base"]).resolve()
        if not base.is_relative_to(ROOT / "results"):
            raise RuntimeError("registered results escape owned directory")
        bases.add(base)
    return bases


def register_fixture():
    base = (ROOT / os.environ.get("RESULTS_DIR", "results/manual")).resolve()
    if not base.is_relative_to(ROOT / "results"):
        raise RuntimeError("owned results directory required")
    identity = fixture_identity()
    if base not in registered_bases(identity):
        history.save(
            ROOT / "results" / ("fixture-registration-" + uuid.uuid4().hex + ".json"),
            {
                "volumes": identity,
                "results_base": str(base.relative_to(ROOT)),
                "utc_ns": time.time_ns(),
                "scope": "Registered before legacy mutations. A different RESULTS_DIR at teardown does not bypass current-fixture ACK verification.",
            },
        )


def owned_result(path):
    path = path.resolve(strict=True)
    if not path.is_relative_to(ROOT / "results"):
        raise RuntimeError("owned results path required")
    return path


def file_sha256(path):
    with owned_result(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def native_fences():
    code = "import sys,json;sys.path.insert(0,'/opt/lab/app');import archive_verify;print(json.dumps(archive_verify.fences()))"
    return json.loads(
        run(
            COMPOSE
            + [
                "run",
                "--rm",
                "--entrypoint",
                "/opt/venv/bin/python",
                "controller",
                "-c",
                code,
            ],
            timeout=120,
        )
    )


def verify_failed_prefixes(base, archive):
    data = json.loads((archive / "result.json").read_text())
    preserved = json.loads((base / "old-export-partials-preserved.json").read_text())
    checked = []
    for item in preserved["partial_files"]:
        partial = owned_result(ROOT / item["external_path"])
        case = partial.stem.removeprefix("live_")
        complete = owned_result(archive / data["csv_files"][case])
        if not complete.is_relative_to(archive):
            raise RuntimeError("failed-prefix reference escapes archive")
        with partial.open("rb") as left, complete.open("rb") as right:
            while block := left.read(1024**2):
                if block != right.read(len(block)):
                    raise RuntimeError(
                        "failed export differs from verified native body prefix: "
                        + case
                    )
        checked.append(
            {
                "case": case,
                "bytes": partial.stat().st_size,
                "complete_csv": str(complete.relative_to(ROOT)),
            }
        )
    if not checked:
        raise RuntimeError("failed export prefixes missing")
    return checked


def validate_retirement(suite):
    value = json.loads((suite / "retirement.json").read_text())
    if (
        value["status"] != "failed-but-archived"
        or value["passed"] is not False
        or value["suite"] != str(suite.relative_to(ROOT))
    ):
        raise RuntimeError("invalid failed-suite retirement")
    for relative, digest in value["evidence_sha256"].items():
        if file_sha256(ROOT / relative) != digest:
            raise RuntimeError("retired failure evidence changed: " + relative)
    archive = owned_result(ROOT / value["archive"])
    if (
        archive.parent != suite.parent
        or file_sha256(archive / "result.json") != value["archive_result_sha256"]
    ):
        raise RuntimeError("retirement archive changed or escaped fixture")
    replay_archive(archive)
    verify_failed_prefixes(suite.parent, archive)
    if not any(
        json.loads(owned_result(ROOT / relative).read_text())["volumes"]
        == value["volumes"]
        and owned_result(
            ROOT / json.loads((ROOT / relative).read_text())["results_base"]
        )
        == suite.parent
        for relative in value["registrations"]
    ):
        raise RuntimeError(
            "retirement fixture identity differs from pre-mutation registration"
        )
    if not value["native_fences"]["passed"]:
        raise RuntimeError("retirement has no successful native fencing proof")
    if fixture_identity() == value["volumes"]:
        native_fences()
    return value


def retire_failed_suite(suite):
    suite = owned_result(suite)
    if not suite.name.startswith("full-suite-") or (suite / "result.json").exists():
        raise RuntimeError("only a failed full-suite attempt can be retired")
    failure = json.loads((suite / "failure.json").read_text())
    steps = failure["steps"]
    if (
        [step["target"] for step in steps]
        != ["disk-test", "crash-test", "replica-test", "verify-archives"]
        or any(step["exit_code"] for step in steps[:-1])
        or steps[-1]["exit_code"] == 0
    ):
        raise RuntimeError(
            "retirement is limited to the diagnosed final export failure"
        )
    base = suite.parent
    volumes = fixture_identity()
    registrations = [
        path
        for path in (ROOT / "results").glob("fixture-registration-*.json")
        if (value := json.loads(path.read_text()))["volumes"] == volumes
        and owned_result(ROOT / value["results_base"]) == base
    ]
    if not registrations:
        raise RuntimeError("failed fixture lacks pre-mutation volume registration")
    archives = list(base.glob("archive-verification-*/result.json"))
    if not archives:
        raise RuntimeError("failed fixture has no complete replacement archive")
    archive = max(archives, key=lambda path: path.stat().st_mtime_ns).parent
    replay_archive(archive)
    fenced = json.loads((base / "old-export-fenced.json").read_text())
    partials = json.loads((base / "old-export-partials-preserved.json").read_text())
    if (
        not fenced["passed"]
        or not fenced["proc_listing_succeeded"]
        or not partials["passed"]
        or not partials["partial_files"]
    ):
        raise RuntimeError("failed native helper/partial evidence incomplete")
    for item in partials["partial_files"]:
        path = owned_result(ROOT / item["external_path"])
        if path.stat().st_size != item["bytes"] or file_sha256(path) != item["sha256"]:
            raise RuntimeError("failed native CSV bytes changed")
    for identity in run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).split():
        if inspect(identity)["Config"]["Labels"].get("com.docker.compose.service") in (
            "controller",
            "live-controller",
        ):
            raise RuntimeError("stop active controllers before retiring failed state")
    prefixes = verify_failed_prefixes(base, archive)
    fences = native_fences()
    evidence = set(registrations) | {
        path for path in suite.rglob("*") if path.is_file()
    }
    for folder in base.glob("archive-*"):
        if (folder / "failure.json").exists():
            evidence.update(path for path in folder.rglob("*") if path.is_file())
    evidence.update(base.glob("old-export-*.json"))
    source = json.loads(
        (base / "failed-source" / "complete-source-provenance.json").read_text()
    )
    if (
        not source["passed"]
        or file_sha256(base / "final-freeze.json") != source["original_freeze_sha256"]
    ):
        raise RuntimeError("original failed-run source archive incomplete")
    for relative, digest in source["files"].items():
        if file_sha256(base / "failed-source" / relative) != digest:
            raise RuntimeError("original source bytes changed: " + relative)
    evidence.add(base / "final-freeze.json")
    for folder in ("failed-native-export-partials", "failed-source"):
        evidence.update(path for path in (base / folder).rglob("*") if path.is_file())
    history.save(
        suite / "retirement.json",
        {
            "status": "failed-but-archived",
            "passed": False,
            "utc_ns": time.time_ns(),
            "suite": str(suite.relative_to(ROOT)),
            "volumes": volumes,
            "archive": str(archive.relative_to(ROOT)),
            "archive_result_sha256": file_sha256(archive / "result.json"),
            "evidence_sha256": {
                str(path.relative_to(ROOT)): file_sha256(path)
                for path in sorted(evidence)
            },
            "native_fences": fences,
            "registrations": [str(path.relative_to(ROOT)) for path in registrations],
            "failed_prefixes_verified": prefixes,
            "scope": "Cleanup of a failed final-render attempt only, after every current-native ACK/body/source-tail is independently exported and replayed. Original suite remains failed. A fresh complete lifecycle must still qualify the changed harness.",
        },
    )
    validate_retirement(suite)


def teardown_guard():
    for identity in run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).split():
        inspect(identity)
    active_base = (ROOT / os.environ.get("RESULTS_DIR", "results/manual")).resolve()
    if not active_base.is_relative_to(ROOT / "results"):
        raise RuntimeError("owned results directory required")
    legacy_bases = registered_bases(fixture_identity())
    if any(
        path.name.split("-")[0]
        in {"disk", "seed", "live", "fault", "promoted", "benchmark"}
        for path in active_base.glob("*.jsonl")
    ):
        legacy_bases.add(active_base)
    for base in legacy_bases:
        archives = list(base.glob("archive-verification-*/result.json"))
        if not archives:
            raise RuntimeError(
                "legacy ACK histories require a complete all-history export: "
                + str(base)
            )
        replay_archive(max(archives, key=lambda path: path.stat().st_mtime_ns).parent)
    for suite in (ROOT / "results").glob("**/full-suite-*/config.json"):
        if (
            not (suite.parent / "result.json").exists()
            or (suite.parent / "failure.json").exists()
            or (suite.parent / "finalization-failure.json").exists()
        ):
            if not (suite.parent / "retirement.json").exists():
                raise RuntimeError(
                    "incomplete full suite must preserve native state: "
                    + str(suite.parent)
                )
            validate_retirement(suite.parent)
            continue
        archives = list(suite.parent.parent.glob("archive-verification-*/result.json"))
        if not archives:
            raise RuntimeError("full suite has no complete native all-history export")
        replay_archive(max(archives, key=lambda path: path.stat().st_mtime_ns).parent)
    for config_path in (ROOT / "results").glob("**/live/*/config.json"):
        root = config_path.parent
        if (
            not (root / "host-result.json").exists()
            or not (root / "result.json").exists()
        ):
            raise RuntimeError(
                "incomplete/failed live case retains native storage: " + str(root)
            )
        config = json.loads(config_path.read_text())
        proof = history.reconcile(root / "rows.csv", root, config)
        expected = json.loads((root / "result.json").read_text())["proof"]
        if proof != {k: v for k, v in expected.items() if k != "gzip1"}:
            raise RuntimeError("archived live proof changed")
        for prior in json.loads((root / "result.json").read_text()).get(
            "prior_cases", []
        ):
            prior_root = root.parent / prior["case"]
            prior_config = json.loads((prior_root / "config.json").read_text())
            verified = history.reconcile(
                root / ("prior-" + prior["case"] + ".csv"), prior_root, prior_config
            )
            if verified != {k: v for k, v in prior["proof"].items() if k != "gzip1"}:
                raise RuntimeError("prior case's post-fault export changed")


def replay_archive(root):
    data = json.loads((root / "result.json").read_text())
    base = root.parent
    if (
        not data["passed"]
        or (root / "failure.json").exists()
        or data["history_sha256"] != history.witness_hashes(base)
    ):
        raise RuntimeError(
            "stale or failed all-history archive; preserve native volumes"
        )

    def csv_file(name):
        path = (root / data["csv_files"][name]).resolve(strict=True)
        if not path.is_relative_to(root.resolve()):
            raise RuntimeError("archive path escaped the example")
        return path

    for case in data["historical_cases"]:
        if case["case"] == "legacy-disk":
            proof = history.reconcile_legacy(
                csv_file("legacy-disk"), base, ["disk"], data["parameters"]["customers"]
            )
        else:
            old = base / "live" / case["case"]
            proof = history.reconcile(
                csv_file(case["case"]),
                old,
                json.loads((old / "config.json").read_text()),
            )
        if proof != case["proof"]:
            raise RuntimeError("historical archive proof changed")
    source = history.reconcile_legacy(
        csv_file("fenced-primary-source"),
        base,
        ["seed", "live", "fault"],
        data["parameters"]["customers"],
        error_labels=["fault"],
    )
    labels = ["seed", "live", "fault", "promoted"]
    if list(base.glob("benchmark-*.jsonl")):
        labels.append("benchmark")
    destination = history.reconcile_legacy(
        csv_file("promoted-standby"),
        base,
        labels,
        data["parameters"]["customers"],
        missing_labels=["fault"],
    )
    if (
        source != data["fenced_primary_source"]
        or destination != data["promoted_standby"]
    ):
        raise RuntimeError("source/destination archive proof changed")


def verify_teardown():
    remaining = {}
    for key, command in (
        ("containers", ["docker", "ps", "-aq"]),
        ("volumes", ["docker", "volume", "ls", "-q"]),
        ("networks", ["docker", "network", "ls", "-q"]),
    ):
        remaining[key] = run(
            command + ["--filter", "label=com.docker.compose.project=" + PROJECT]
        ).split()
    remaining["built_image_tag"] = run(
        ["docker", "image", "ls", "-q", "--filter", "reference=duckdb-zfs-lab:dev"]
    ).split()
    remaining["passed"] = not any(remaining.values())
    history.save(
        ROOT / "results" / ("teardown-" + uuid.uuid4().hex + ".json"), remaining
    )
    if not remaining["passed"]:
        raise RuntimeError("owned resources remain after cleanup: " + str(remaining))


def verify_archives():
    base = (ROOT / os.environ.get("RESULTS_DIR", "results/manual")).resolve()
    if not base.is_relative_to(ROOT / "results"):
        raise RuntimeError("owned results directory required")
    root = base / ("archive-controller-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    primary = run(COMPOSE + ["ps", "--all", "-q", "primary"])
    before = inspect(primary)
    history.save(root / "primary-before.json", state(before))
    if not before["State"]["Running"]:
        if before["State"]["Pid"]:
            raise RuntimeError("old QEMU process not fenced")
        run(["docker", "start", primary])
    wait_healthy(primary)
    # The native helper requires the old primary's service PID0 and inactive
    # state before opening only independently named recovery clones.
    name = PROJECT + "-archive-" + uuid.uuid4().hex
    container = None
    failure = None
    try:
        container = run(
            COMPOSE
            + [
                "run",
                "-d",
                "--name",
                name,
                "--entrypoint",
                "/opt/venv/bin/python",
                "controller",
                "/opt/lab/app/archive_verify.py",
            ]
        )
        history.save(root / "controller-started.json", state(inspect(container)))
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            current = inspect(container)
            if not current["State"]["Running"]:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("archive controller deadline exceeded")
        history.save(root / "controller-stopped.json", state(current))
        if current["State"]["ExitCode"]:
            raise RuntimeError("archive verification failed; native storage retained")
    except BaseException:
        failure = traceback.format_exc()
        history.save(root / "failure.json", {"passed": False, "error": failure})
        raise
    finally:
        if container:
            try:
                if inspect(container)["State"]["Running"]:
                    run(["docker", "stop", "-t", "30", container], timeout=45)
                stopped = inspect(container)
                history.save(root / "controller-final.json", state(stopped))
                if stopped["State"]["Running"]:
                    raise RuntimeError("archive controller failed to stop")
                with (root / "controller.log").open("x") as stream:
                    subprocess.run(
                        ["docker", "logs", container],
                        check=True,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        timeout=30,
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                history.save(
                    root / "cleanup-failure.json",
                    {"error": traceback.format_exc(), "original_error": failure},
                )
                if failure is None:
                    raise
    run(["docker", "rm", container])
    history.save(root / "result.json", {"passed": True})
    print(str(root.relative_to(ROOT)), flush=True)


def full_test():
    base = (ROOT / os.environ.get("RESULTS_DIR", "results/manual")).resolve()
    if not base.is_relative_to(ROOT / "results"):
        raise RuntimeError("owned results directory required")
    root = base / ("full-suite-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    config = {
        "source_sha256": source_hashes(),
        "targets": ["disk-test", "crash-test", "replica-test", "verify-archives"],
        "rows": int(os.environ.get("ROWS", "2000000")),
    }
    history.save(root / "config.json", config)
    names = {
        "primary": PROJECT + "-primary-1",
        "standby": PROJECT + "-standby-1",
        "controller": "@controllers",
    }
    for role in ("primary", "standby"):
        if not inspect(names[role])["State"]["Running"]:
            raise RuntimeError("full suite requires both provisioned guests")
    collector = Resources(root, names)
    steps = []
    try:
        for target in config["targets"]:
            started = time.monotonic_ns()
            with (root / (target + ".log")).open("x") as stream:
                process = subprocess.Popen(
                    ["make", target],
                    cwd=ROOT,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=3600)
                except BaseException:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=40)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                    for identity in run(
                        [
                            "docker",
                            "ps",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + PROJECT,
                        ]
                    ).split():
                        container = inspect(identity)
                        if container["Config"]["Labels"].get(
                            "com.docker.compose.service"
                        ) in ("controller", "live-controller"):
                            run(["docker", "stop", "-t", "30", identity], timeout=45)
                    raise
                stream.flush()
                os.fsync(stream.fileno())
            steps.append(
                {
                    "target": target,
                    "wall_ns": time.monotonic_ns() - started,
                    "exit_code": process.returncode,
                }
            )
            history.save(root / (target + ".json"), steps[-1])
            if process.returncode:
                raise RuntimeError("full suite failed: " + target)
        if source_hashes() != config["source_sha256"]:
            raise RuntimeError("source changed during full suite")
    except BaseException:
        history.save(
            root / "failure.json",
            {"passed": False, "steps": steps, "error": traceback.format_exc()},
        )
        raise
    finally:
        try:
            collector.finish()
            summary = resource_summary(root)
            history.save(root / "resource-summary.json", summary)
            if any(
                summary[role]["observed_cpu_core_seconds"] is None
                or summary[role]["memory_sampled_max_bytes"] is None
                for role in ("primary", "standby", "controller")
            ):
                raise RuntimeError("missing full-suite active-role resource evidence")
        except BaseException:
            history.save(
                root / "finalization-failure.json",
                {"passed": False, "error": traceback.format_exc()},
            )
            if not (root / "failure.json").exists():
                raise
    history.save(root / "result.json", {"passed": True, "steps": steps})
    print(str(root.relative_to(ROOT)), flush=True)


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to(ROOT / "results"):
        raise RuntimeError("select an example-owned result directory")

    def load(name):
        return json.loads((root / name).read_text())

    config, result, host = (
        load("config.json"),
        load("result.json"),
        load("host-result.json"),
    )
    if (
        not result["passed"]
        or not host["passed"]
        or any(
            (root / name).exists()
            for name in (
                "failure.json",
                "host-failure.json",
                "cleanup-failure.json",
                "configuration-drift.json",
            )
        )
    ):
        raise RuntimeError("failed/incomplete case cannot qualify")
    if config["source_sha256"] != source_hashes() or config["rows"] < 1000000:
        raise RuntimeError("require tested sources and at least one million seed rows")
    if (
        hashlib.sha256((root / "result.json").read_bytes()).hexdigest()
        != host["result_sha256"]
    ):
        raise RuntimeError("controller result changed")
    proof = history.reconcile(root / "rows.csv", root, config)
    if proof != {k: v for k, v in result["proof"].items() if k != "gzip1"}:
        raise RuntimeError("independent final-body replay differs")
    env = load("environment.json")
    resources = load("resource-summary.json")
    for role in ("primary", "controller"):
        if (
            resources[role]["observed_cpu_core_seconds"] is None
            or resources[role]["memory_sampled_max_bytes"] is None
        ):
            raise RuntimeError("missing active-role CPU/memory observations")
    source = load("before.json")
    reopened, settled = load("recovered.json"), load("settled.json")
    if (
        source["boot_id"] == reopened["boot_id"]
        or source["pool"]["health"] != "ONLINE"
        or settled["pool"]["health"] != "ONLINE"
    ):
        raise RuntimeError("native crash/recovery proof differs")

    def storage(value):
        return {
            parts[1]: parts[2]
            for line in value["storage"].splitlines()
            if len(parts := line.split("\t")) == 3 and parts[0] == "ducklab/data"
        }

    report = {
        "passed": True,
        "case": root.name,
        "raw_directory": str(root.relative_to(ROOT)),
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": config,
        "proof": result["proof"],
        "prior_case_native_rechecks": result.get("prior_cases", []),
        "availability": availability(root),
        "phases": result["phases"],
        "resources": resources,
        "actors": result["actors"],
        "controller_process_resources": result["controller"],
        "actor_resources": {
            p.stem: json.loads(p.read_text()) for p in root.glob("*-resources.json")
        },
        "native_artifacts": load("native-artifacts.json"),
        "settings": load("settings.json"),
        "workload_settings": load("workload-settings.json"),
        "environment": {
            "cpu_model": env["cpu_model"],
            "host_architecture": env["host_architecture"],
            "host_kernel": env["host_kernel"],
            "docker": {
                key: env["docker"][key]
                for key in (
                    "ServerVersion",
                    "KernelVersion",
                    "Architecture",
                    "NCPU",
                    "MemTotal",
                    "Driver",
                    "CgroupVersion",
                )
            },
            "vms": env["vms"],
            "co_tenancy": env["co_tenancy"],
        },
        "native_storage": {
            "after_seed": storage(source),
            "after_reopen_actors_live": storage(reopened),
            "after_actors_stopped": storage(settled),
        },
        "scope": "One functional2m-row retained-disk QEMU/TCG crash observation; not native capacity, independent-host durability or HA. Standby inactive. Failed setup and all raw histories remain preserved. Successful/error latencies separate; p99 from small phase counts is near a maximum, not an established production tail. Cold-cache, physical disk loss, partitions, pressure, repeated recovery-performance comparisons and remaining deployment matrices are unrun.",
    }
    # Published summaries are selected fields; full runtime inspections stay ignored.
    for value in report["environment"]["vms"].values():
        value["state"] = {
            key: value["state"][key]
            for key in ("Running", "ExitCode", "StartedAt", "FinishedAt")
        }
    path = ROOT / "LIVE_RECOVERY_RESULTS.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(path.name)
    return report


if __name__ == "__main__":

    def stopped(*_):
        raise KeyboardInterrupt("stop requested")

    signal.signal(signal.SIGTERM, stopped)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "crash",
            "register-fixture",
            "retire-failed-suite",
            "full-test",
            "verify-archives",
            "teardown-guard",
            "verify-teardown",
            "report",
        ),
    )
    parser.add_argument("case", nargs="?", type=Path)
    args = parser.parse_args()
    if args.action == "crash":
        crash()
    elif args.action == "register-fixture":
        register_fixture()
    elif args.action == "retire-failed-suite":
        if args.case is None:
            parser.error("retire-failed-suite requires the failed full-suite directory")
        retire_failed_suite(args.case)
    elif args.action == "full-test":
        full_test()
    elif args.action == "verify-archives":
        verify_archives()
    elif args.action == "report":
        if args.case is None:
            parser.error("report requires results/<run>/live/<case>")
        publish(args.case)
    elif args.action == "verify-teardown":
        verify_teardown()
    else:
        teardown_guard()
