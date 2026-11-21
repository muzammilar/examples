"""Restore a fenced snapshot/journal copy into separate owned local storage."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import time
import uuid
import zlib

import experiment as bench
import ingestion
from journal import inspect_owned
from live_journal import reconcile


WRAPPER = r"""
import json, os, pathlib, resource, subprocess, time
inputs = pathlib.Path('/data')
if sorted(p.name for p in inputs.iterdir()) != ['.isolation-token'] or (inputs / '.isolation-token').read_text() != os.environ['JOURNAL_INPUT_TOKEN']:
    raise RuntimeError('Original input isolation was not proved')
started = time.monotonic_ns()
code = subprocess.run(['/opt/rayforce/rayforce', '-c', '1', '-m', '3G', '-L', '/state/journal', '-f', '/state/verify.rfl']).returncode
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
metrics = {'exit_code': code, 'wall_ns': time.monotonic_ns()-started,
 'process_cpu_core_seconds': usage.ru_utime+usage.ru_stime,
 'process_peak_rss_bytes': usage.ru_maxrss*1024,
 'cgroup': {name: pathlib.Path('/sys/fs/cgroup', name).read_text() for name in ('cpu.stat','memory.peak','memory.events','io.stat')}}
print('__RESTORE_METRICS__'+json.dumps(metrics), flush=True)
raise SystemExit(code)
"""


def fingerprint(paths):
    result = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("native state must contain only regular files")
        with path.open("rb") as stream:
            result[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
            }
    return result


def archive_compressibility(paths):
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    files = []
    for path in paths:
        codec = zlib.compressobj(1, zlib.DEFLATED, 31)
        raw, compressed = 0, 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                raw += len(chunk)
                compressed += len(codec.compress(chunk))
        compressed += len(codec.flush())
        files.append(
            {
                "name": path.name,
                "bytes": raw,
                "gzip_level_1_bytes": compressed,
                "allocated_bytes": path.stat().st_blocks * 512,
            }
        )
    return {
        "files": files,
        "wall_ns": time.monotonic_ns() - started,
        "cpu_ns": time.process_time_ns() - cpu,
        "zlib": zlib.ZLIB_VERSION,
        "scope": "Host-side streamed gzip compressibility of stable native files, not native engine compression",
    }


def restore(source):
    host_started = time.monotonic_ns()
    host_before = resource.getrusage(resource.RUSAGE_SELF)
    source = source.resolve(strict=True)
    if not source.is_relative_to((bench.RESULTS / "journal-live").resolve()):
        raise ValueError("source must be this example's completed journal-live case")
    original = json.loads((source / "result.json").read_text())
    if not original["passed"] or not original.get("finalized_after_cleanup"):
        raise RuntimeError("source must have a successful fenced teardown")
    if bench.capture(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=rayforce-duckdb-journal",
        ],
        text=True,
    ).strip():
        raise RuntimeError("journal project is not fenced/empty")
    setup = json.loads((source / "setup.json").read_text())
    for name, digest in setup["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("source input fixture differs")
    root = bench.RESULTS / "journal-restore" / uuid.uuid4().hex
    state = root / "state"
    state.mkdir(parents=True)
    empty_input = root / "empty-input"
    empty_input.mkdir()
    input_token = uuid.uuid4().hex
    (empty_input / ".isolation-token").write_text(input_token)
    ingestion.save(
        root / "environment.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "cpu_model": bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if os.uname().sysname == "Darwin"
            else "Unresolved; supply native host CPU inventory",
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "storage": "New copied local bind-mount files on the same Docker Desktop/host filesystem; physical IOPS/latency unavailable",
            "client": "No RPC for this offline restore: native CLI startup recovery and local boot script; 120s whole-container deadline, no retries. Controller alone reads independent input/witness files.",
        },
    )
    paths = sorted((source / "state").glob("journal*"))
    before = fingerprint(paths)
    if not {"journal.log", "journal.qdb"} <= before.keys():
        raise RuntimeError("snapshot and journal tail are both required")
    started = time.monotonic_ns()
    for path in paths:
        target = state / path.name
        shutil.copyfile(path, target)
        with target.open("rb") as stream:
            os.fsync(stream.fileno())
    copied = fingerprint(sorted(state.glob("journal*")))
    if copied != before:
        raise RuntimeError("copy differs from the fenced source")
    ingestion.save(
        root / "copy-proof.json",
        {
            "source": str(source.relative_to(bench.ROOT)),
            "files": copied,
            "wall_ns": time.monotonic_ns() - started,
            "scope": "Fenced offline copy to separate owned local files; same host/physical disk, not off-host durability or a live backup",
        },
    )
    (state / "verify.rfl").write_text(
        "(println (count trades))\n"
        '(.csv.write (select {from: trades asc: id}) "/state/restored.csv")\n'
        '(.csv.write (select {from: customers asc: customer}) "/state/restored-customers.csv")\n'
        "(exit 0)\n"
    )
    name = "rayforce-journal-restore-" + root.name[:8]
    env = dict(
        os.environ,
        JOURNAL_STORAGE_PATH=str(state),
        JOURNAL_INPUT_PATH=str(empty_input),
        LAB_UID=str(os.getuid()),
        LAB_GID=str(os.getgid()),
    )
    compose = [
        "docker",
        "compose",
        "-f",
        str(bench.ROOT / "docker-compose.journal.yml"),
    ]
    samples, disks = [], [bench.storage_sample(state)]
    launched = False
    result = None
    try:
        subprocess.run(
            compose
            + [
                "run",
                "-d",
                "--no-deps",
                "--name",
                name,
                "--env",
                "JOURNAL_INPUT_TOKEN=" + input_token,
                "--entrypoint",
                "python",
                "rayforce",
                "-c",
                WRAPPER,
            ],
            env=env,
            check=True,
            timeout=60,
        )
        launched = True
        endpoint = bench.docker_socket()
        deadline = time.monotonic() + 120
        while True:
            inspection = inspect_owned(name)
            if not inspection["State"]["Running"]:
                break
            sample = bench.resource_sample(endpoint, inspection["Id"])
            if sample is not None:
                samples.append(sample)
            disks.append(bench.storage_sample(state))
            if time.monotonic() > deadline:
                raise TimeoutError("native snapshot/tail restore exceeded 120 seconds")
            time.sleep(0.1)
        disks.append(bench.storage_sample(state))
        ingestion.save(root / "inspection.json", inspection)
        ingestion.save(root / "samples.json", {"resources": samples, "disk": disks})
        if not any(
            m["Destination"] == "/data"
            and m["Source"] in {str(empty_input), "/host_mnt" + str(empty_input)}
            and not m["RW"]
            for m in inspection["Mounts"]
        ):
            raise RuntimeError("restore can still reach the original input files")
        config = inspection["HostConfig"]
        if (
            config["Memory"] != 4 * 1024**3
            or config["MemorySwap"] != 4 * 1024**3
            or config["NanoCpus"] != 10**9
        ):
            raise RuntimeError("effective restore budgets differ")
        if "RAYFORCE_NO_FSYNC=0" not in inspection["Config"]["Env"]:
            raise RuntimeError("native fsync is not explicitly enabled")
        log = bench.capture(
            ["docker", "logs", name], text=True, stderr=subprocess.STDOUT
        )
        (root / "native.log").write_text(log)
        if inspection["State"]["ExitCode"] or "__RESTORE_METRICS__" not in log:
            raise RuntimeError("native restore did not complete; preserve diagnostics")
        metrics = json.loads(log.split("__RESTORE_METRICS__")[-1].splitlines()[0])
        if not any("(0 eval errors)" in line for line in log.splitlines()):
            raise RuntimeError("native replay did not prove zero evaluation errors")
        events = [
            json.loads(line)
            for line in (source / "history.jsonl").read_text().splitlines()
        ]
        proof = reconcile(
            bench.RESULTS / "data/trades.csv", state / "restored.csv", events
        )
        dimension = bench.RESULTS / "data/customers.csv"
        customers = ingestion.verify_export(
            state / "restored-customers.csv",
            {
                "rows": 10000,
                "bytes": dimension.stat().st_size,
                "sha256": setup["fixture"]["sha256"]["customers.csv"],
            },
        )
        if fingerprint(paths) != before:
            raise RuntimeError("offline source changed during restore")
        compressed = archive_compressibility(sorted(state.glob("journal*")))
        host_after = resource.getrusage(resource.RUSAGE_SELF)
        result = {
            "passed": False,
            "source_case": str(source.relative_to(bench.ROOT)),
            "copy": copied,
            "proof": proof,
            "customers": customers,
            "native": metrics,
            "sampled_resources": bench.resource_summary(samples),
            "native_block_io": bench.io_counters(
                metrics["cgroup"]["io.stat"], metrics["wall_ns"] / 1e9
            ),
            "archive_compressibility": compressed,
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    bench.ROOT / "docker/Dockerfile",
                    bench.ROOT / "docker-compose.journal.yml",
                    bench.ROOT / "live_journal.py",
                ]
            },
            "host_controller": {
                "wall_ns": time.monotonic_ns() - host_started,
                "cpu_core_seconds": host_after.ru_utime
                + host_after.ru_stime
                - host_before.ru_utime
                - host_before.ru_stime,
                "peak_rss_bytes": host_after.ru_maxrss
                * (1 if os.uname().sysname == "Darwin" else 1024),
                "scope": "Controller process including copy/hash/oracle/gzip; CLI subprocess/daemon/VM costs excluded",
            },
            "scope": "Native snapshot plus journal-tail restore into separate local files, full independent old/ACK body oracle; offline verification, not live failover/backup availability or physical host loss",
        }
        ingestion.save(root / "result.json", result)
    finally:
        if launched:
            inspection = inspect_owned(name)
            if inspection["State"]["Running"]:
                subprocess.run(["docker", "kill", name], check=True, timeout=30)
            subprocess.run(["docker", "rm", name], check=True, timeout=30)
        subprocess.run(compose + ["down"], env=env, check=True, timeout=60)
    result["passed"] = True
    result["finalized_after_cleanup"] = True
    ingestion.save(root / "result.json", result)
    print("Offline snapshot/tail restore passed:", root, proof, flush=True)


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "journal-restore").resolve()):
        raise ValueError("publish requires this example's journal-restore directory")
    result = json.loads((root / "result.json").read_text())
    if not result["passed"] or not result.get("finalized_after_cleanup"):
        raise RuntimeError("restore did not pass and finish cleanup")
    for name, digest in result["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    inspection = json.loads((root / "inspection.json").read_text())
    ingestion.save(
        bench.ROOT / "RESTORE_RESULTS.json",
        {
            "source_directory": str(root.relative_to(bench.ROOT)),
            "result": result,
            "environment": json.loads((root / "environment.json").read_text()),
            "copy_proof": json.loads((root / "copy-proof.json").read_text()),
            "image_id": inspection["Image"],
            "effective_limits": {
                name: inspection["HostConfig"][name]
                for name in ("Memory", "MemorySwap", "NanoCpus", "ReadonlyRootfs")
            },
            "mounts": inspection["Mounts"],
            "unrun": [
                "online backup/restore availability",
                "off-host storage failure",
                "second-writer exclusion",
                "torn-tail/interior corruption repair",
                "matched DuckDB recovery",
                "repeated sustained performance",
            ],
        },
    )
    print("Published explicit offline snapshot/journal restore evidence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_case", type=Path)
    parser.add_argument("--publish", action="store_true")
    arguments = parser.parse_args()
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if arguments.publish:
            publish(arguments.source_case)
        else:
            restore(arguments.source_case)
