"""Controlled offline torn tail: fail closed, validate, repair a separate copy."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import time
import uuid

import experiment as bench
import ingestion
from journal import inspect_owned
from journal_restore import archive_compressibility, fingerprint
import journal_volume
from live_journal import reconcile


VERIFY = """(println "BOOT_REACHED")
(.csv.write (select {from: trades asc: id}) "/state/restored.csv")
(.csv.write (select {from: customers asc: customer}) "/state/restored-customers.csv")
(exit 0)
"""
VALIDATE = """(set report (.log.validate "/state/journal.log"))
(println (first report))
(println (last report))
(exit 0)
"""


def require_empty_project():
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


def clone_native(source, destination):
    destination.mkdir(parents=True)
    for path in sorted(source.glob("journal*")):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("native source contains a non-regular file")
        shutil.copyfile(path, destination / path.name)
    (destination / "verify.rfl").write_text(VERIFY)
    (destination / "validate.rfl").write_text(VALIDATE)


def repair_known_tail(damaged, repaired, validated_bytes, known_log):
    """Only remove our exact synthetic trailer from a verified known prefix."""
    log = damaged / "journal.log"
    if (
        log.is_symlink()
        or validated_bytes != known_log["bytes"]
        or log.stat().st_size != validated_bytes + 3
    ):
        raise ValueError("repair is not the known three-byte tail")
    digest = hashlib.sha256()
    with log.open("rb") as stream:
        remaining = validated_bytes
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("known prefix is incomplete")
            digest.update(chunk)
            remaining -= len(chunk)
        if (
            stream.read() != b"\x01\x02\x03"
            or digest.hexdigest() != known_log["sha256"]
        ):
            raise ValueError("unknown trailer or altered prefix; do not truncate")
    clone_native(damaged, repaired)
    with (repaired / "journal.log").open("r+b") as stream:
        stream.truncate(validated_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    if fingerprint([repaired / "journal.log"])["journal.log"] != known_log:
        raise RuntimeError("repair is not byte-identical to the original known prefix")


def native_job(root, validate_only=False, aof_mode=None):
    if aof_mode not in (None, "seed", "scan", "open"):
        raise ValueError("unknown native AOF operation")
    require_empty_project()
    state = root / "state"
    empty_input = root / "empty-input"
    empty_input.mkdir()
    input_token = uuid.uuid4().hex
    (empty_input / ".isolation-token").write_text(input_token)
    volume = "rayforce-journal-tail-" + uuid.uuid4().hex
    env = dict(
        os.environ,
        JOURNAL_STORAGE_PATH=str(state),
        JOURNAL_INPUT_PATH=str(empty_input),
        JOURNAL_INPUT_TOKEN=input_token,
        JOURNAL_VOLUME=volume,
        JOURNAL_VALIDATE_ONLY="1" if validate_only else "0",
        AOF_MODE=aof_mode or "",
        LAB_UID=str(os.getuid()),
        LAB_GID=str(os.getgid()),
    )
    compose = [
        "docker",
        "compose",
        "-f",
        str(bench.ROOT / "docker-compose.journal.yml"),
        "-f",
        str(bench.ROOT / "docker-compose.journal-volume.yml"),
        "-f",
        str(bench.ROOT / "docker-compose.journal-tail.yml"),
    ]
    if aof_mode:
        compose += ["-f", str(bench.ROOT / "docker-compose.aof.yml")]
    compose += ["--profile", "tools"]
    samples, disks = [], []
    server = None
    exported = False
    metrics = None
    try:
        journal_volume.initialize(compose, env, root)
        subprocess.run(
            compose + ["create", "rayforce"], env=env, check=True, timeout=30
        )
        server = subprocess.check_output(
            compose + ["ps", "-aq", "rayforce"], env=env, text=True, timeout=30
        ).strip()
        info = inspect_owned(server)
        ingestion.save(root / "before-inspection.json", info)
        if not any(
            m["Type"] == "volume"
            and m.get("Name") == volume
            and m["Destination"] == "/state"
            for m in info["Mounts"]
        ):
            raise RuntimeError("native job mounted another state volume")
        if not any(
            m["Destination"] == "/data"
            and m["Source"] in {str(empty_input), "/host_mnt" + str(empty_input)}
            and not m["RW"]
            for m in info["Mounts"]
        ):
            raise RuntimeError("native job can still access original input CSVs")
        if "RAYFORCE_NO_FSYNC=0" not in info["Config"]["Env"]:
            raise RuntimeError("native fsync is not explicitly enabled")
        config = info["HostConfig"]
        if (
            config["NanoCpus"] != 10**9
            or config["Memory"] != 4 * 1024**3
            or config["MemorySwap"] != 4 * 1024**3
        ):
            raise RuntimeError("native resource budget differs")
        subprocess.run(
            ["docker", "cp", "--archive", str(state) + "/.", server + ":/state/"],
            check=True,
            timeout=60,
        )
        subprocess.run(["docker", "start", server], check=True, timeout=30)
        endpoint = bench.docker_socket()
        deadline = time.monotonic() + 120
        while True:
            info = inspect_owned(server)
            if not info["State"]["Running"]:
                break
            value = bench.resource_sample(endpoint, server)
            if value:
                samples.append(value)
            try:
                disks.append(journal_volume.sample(server))
            except subprocess.CalledProcessError:
                if inspect_owned(server)["State"]["Running"]:
                    raise
                disks.append(
                    {
                        "available": False,
                        "files": {},
                        "reason": "native job ended during volume sampling",
                    }
                )
            if time.monotonic() > deadline:
                raise TimeoutError("native journal job exceeded 120 seconds")
            time.sleep(0.1)
        ingestion.save(root / "after-inspection.json", info)
        ingestion.save(root / "samples.json", {"resources": samples, "disks": disks})
        raw = bench.capture(
            ["docker", "logs", server], text=True, stderr=subprocess.STDOUT
        )
        (root / "native.log").write_text(raw)
        records = [
            json.loads(line.removeprefix("__JOURNAL_JOB__"))
            for line in raw.splitlines()
            if line.startswith("__JOURNAL_JOB__")
        ]
        if len(records) != 1 or records[0]["exit_code"] != info["State"]["ExitCode"]:
            raise RuntimeError("native exit/metrics are incomplete")
        journal_volume.export(server, state, root)
        exported = True
        metrics = records[0]
        metrics["sampled_resources"] = bench.resource_summary(samples)
        metrics["container_lifetime_seconds"] = bench.container_seconds(info)
        metrics["native_process_cpu_percent_one_core_100"] = (
            100 * metrics["process_cpu_core_seconds"] / (metrics["wall_ns"] / 1e9)
        )
        metrics["block_io"] = bench.io_counters(
            metrics["cgroup"]["io.stat"], metrics["container_lifetime_seconds"]
        )
        metrics["block_io"]["rate_scope"] = (
            "Whole-container counters over StartedAt-to-FinishedAt lifetime; native-child wall is narrower."
        )
        native_files = sorted(state.glob("*.aof" if aof_mode else "journal*"))
        metrics["native_files"] = fingerprint(native_files)
        metrics["compressibility"] = archive_compressibility(native_files)
        ingestion.save(root / "native-result.json", metrics)
    finally:
        if server is not None and not exported:
            # Preserve the only native volume if fencing/export cannot be proved.
            try:
                journal_volume.export(server, state, root)
                exported = True
            finally:
                subprocess.run(
                    compose
                    + ["down", "--remove-orphans"]
                    + (["--volumes"] if exported else []),
                    env=env,
                    check=True,
                    timeout=60,
                )
        else:
            subprocess.run(
                compose
                + ["down", "--remove-orphans"]
                + (["--volumes"] if exported else []),
                env=env,
                check=True,
                timeout=60,
            )
        require_empty_project()
        if (
            exported
            and volume
            in bench.capture(["docker", "volume", "ls", "-q"], text=True).splitlines()
        ):
            raise RuntimeError("owned native volume remains after teardown")
    return metrics


def run(source):
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    source = source.resolve(strict=True)
    if not source.is_relative_to((bench.RESULTS / "journal-live").resolve()):
        raise ValueError("select an owned completed journal-live case")
    prior = json.loads((source / "result.json").read_text())
    if not prior["passed"] or not prior.get("finalized_after_cleanup"):
        raise RuntimeError("source must have completed correctness and teardown")
    require_empty_project()
    setup = json.loads((source / "setup.json").read_text())
    for name, digest in setup["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("independent input fixture differs")
    root = bench.RESULTS / "journal-tail" / uuid.uuid4().hex
    root.mkdir(parents=True)
    ingestion.save(
        root / "environment.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "cpu_model": bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if os.uname().sysname == "Darwin"
            else "Unresolved; supply native CPU inventory",
            "fixture": setup["fixture"],
            "pins": json.loads((bench.ROOT / "sources.json").read_text()),
            "docker_info": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "source_sha256": {
                str(p.relative_to(bench.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    bench.ROOT / "journal_volume.py",
                    bench.ROOT / "journal_restore.py",
                    bench.ROOT / "live_journal.py",
                    bench.ROOT / "docker-compose.journal.yml",
                    bench.ROOT / "docker-compose.journal-volume.yml",
                    bench.ROOT / "docker-compose.journal-tail.yml",
                    bench.ROOT / "docker/Dockerfile",
                ]
            },
            "scope": "Three serial offline native jobs, fresh owned Linux volumes, 1 CPU/4GiB swap disabled. Witness/oracle outside the volume. Not live chaos, physical disk failure or power-loss qualification.",
        },
    )
    original = fingerprint(sorted((source / "state").glob("journal*")))
    if not {"journal.log", "journal.qdb"} <= original.keys():
        raise RuntimeError("native source needs snapshot and tail")
    damaged = root / "damaged" / "state"
    clone_native(source / "state", damaged)
    with (damaged / "journal.log").open("ab") as stream:
        stream.write(b"\x01\x02\x03")
        stream.flush()
        os.fsync(stream.fileno())
    damaged_hash = fingerprint([damaged / "journal.log"])
    ingestion.save(
        root / "fault.json",
        {
            "source": str(source.relative_to(bench.ROOT)),
            "original": original,
            "appended_hex": "010203",
            "bytes_added": 3,
            "damaged_log": damaged_hash,
        },
    )
    bad = native_job(damaged.parent)
    raw = (damaged.parent / "native.log").read_text()
    if (
        bad["exit_code"] != 2
        or "ERROR badtail" not in raw
        or "BOOT_REACHED" in raw
        or "listening on" in raw
    ):
        raise RuntimeError(
            "torn-tail native startup did not fail closed before boot/listen"
        )
    if fingerprint([damaged / "journal.log"]) != damaged_hash:
        raise RuntimeError("failed startup unexpectedly altered the torn journal")
    validate = root / "validate" / "state"
    clone_native(damaged, validate)
    checked = native_job(validate.parent, validate_only=True)
    numbers = [
        int(line)
        for line in (validate.parent / "native.log").read_text().splitlines()
        if re.fullmatch(r"\d+", line)
    ]
    if checked["exit_code"] or len(numbers) != 2:
        raise RuntimeError("native validate output is incomplete")
    chunks, valid_bytes = numbers
    if chunks < 1 or valid_bytes != original["journal.log"]["bytes"]:
        raise RuntimeError(
            "native validated prefix differs from the known intact source"
        )
    repaired = root / "repaired" / "state"
    if checked["native_files"] != fingerprint(sorted(damaged.glob("journal*"))):
        raise RuntimeError("read-only validation changed the native state")
    repair_known_tail(damaged, repaired, valid_bytes, original["journal.log"])
    recovered = native_job(repaired.parent)
    raw = (repaired.parent / "native.log").read_text()
    if (
        recovered["exit_code"]
        or "(0 eval errors)" not in raw
        or "BOOT_REACHED" not in raw
    ):
        raise RuntimeError("repaired copy did not replay cleanly")
    events = [
        json.loads(line) for line in (source / "history.jsonl").read_text().splitlines()
    ]
    proof = reconcile(
        bench.RESULTS / "data/trades.csv", repaired / "restored.csv", events
    )
    dimension = bench.RESULTS / "data/customers.csv"
    customers = ingestion.verify_export(
        repaired / "restored-customers.csv",
        {
            "rows": 10000,
            "bytes": dimension.stat().st_size,
            "sha256": setup["fixture"]["sha256"]["customers.csv"],
        },
    )
    if (
        fingerprint(sorted((source / "state").glob("journal*"))) != original
        or fingerprint([damaged / "journal.log"]) != damaged_hash
    ):
        raise RuntimeError("original or damaged evidence changed during repair")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    ingestion.save(
        root / "result.json",
        {
            "passed": True,
            "source": str(source.relative_to(bench.ROOT)),
            "fault_bytes_added": 3,
            "native_validated_chunks": chunks,
            "native_validated_prefix_bytes": valid_bytes,
            "native_jobs": {
                "damaged_startup": bad,
                "read_only_validation": checked,
                "repaired_startup": recovered,
            },
            "proof": proof,
            "customers": customers,
            "original_and_damaged_preserved": True,
            "host_controller": {
                "wall_ns": time.monotonic_ns() - started,
                "cpu_ns": time.process_time_ns() - cpu,
                "peak_rss_bytes": usage.ru_maxrss
                * (1 if os.uname().sysname == "Darwin" else 1024),
                "scope": "Controller including copy/hash/oracle/gzip, excludes CLI/daemon/VM",
            },
            "scope": "Controlled three-byte synthetic torn tail on an offline copy. Native fail-closed startup and validated-prefix repair into another fresh copy, zero original ACK loss. Does not justify truncating unknown production corruption or qualify interior-corruption repair.",
        },
    )
    print("Controlled torn-tail recovery passed:", root, proof, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_case", type=Path)
    args = parser.parse_args()
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.source_case)
