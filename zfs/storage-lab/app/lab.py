"""Storage-only ZFS experiments, using upstream fio and ZFS CLI tools."""

import json
import uuid

import evidence
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

RESULTS = Path("/results")
NODES = ("source", "target")
SSH = [
    "ssh",
    "-i",
    "/keys/id_ed25519",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "LogLevel=ERROR",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=6",
    "-p",
    "2222",
]


def ssh_args(node, command):
    if node not in NODES:
        raise ValueError("unknown node")
    return SSH + ["lab@" + node, shlex.join(["sudo", "-n", "bash", "-euc", command])]


def ssh(node, command, timeout=120):
    p = subprocess.run(
        ssh_args(node, command), capture_output=True, text=True, timeout=timeout
    )
    if p.returncode:
        raise RuntimeError(
            f"{node} exit={p.returncode}: {command}\n{p.stdout[-500:]}\n{p.stderr[-3000:]}"
        )
    return p.stdout.strip()


def save(name, value):
    RESULTS.mkdir(exist_ok=True)
    records = RESULTS / "records"
    records.mkdir(exist_ok=True)
    evidence.save(records / (name + "-" + uuid.uuid4().hex + ".json"), value)
    with (RESULTS / (name + ".json")).open("w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    print(name, json.dumps(value), flush=True)


def replicate(
    source, dest, name, previous=None, resume_token=None, interrupt_bytes=None
):
    send = "zfs send " + (
        "-t " + shlex.quote(resume_token)
        if resume_token
        else (("-i zfslab/data@" + previous + " ") if previous else "")
        + "zfslab/data@"
        + name
    )
    receive = "zfs receive -s -u -o readonly=on zfslab/data"
    start = time.monotonic()
    total = 0
    with tempfile.TemporaryFile() as se, tempfile.TemporaryFile() as re:
        producer = subprocess.Popen(
            ssh_args(source, send), stdout=subprocess.PIPE, stderr=se
        )
        consumer = subprocess.Popen(
            ssh_args(dest, receive),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=re,
        )
        try:
            while True:
                b = producer.stdout.read(65536)
                if not b:
                    break
                consumer.stdin.write(b)
                consumer.stdin.flush()
                total += len(b)
                if interrupt_bytes and total >= interrupt_bytes:
                    break
        finally:
            if interrupt_bytes:
                producer.terminate()
            producer.stdout.close()
            consumer.stdin.close()
        a = producer.wait(timeout=60)
        b = consumer.wait(timeout=60)
        if not interrupt_bytes and (a or b):
            se.seek(0)
            re.seek(0)
            raise RuntimeError(
                f"replication {a}/{b}: {se.read()[-1500:]} {re.read()[-1500:]}"
            )
    return {
        "bytes": total,
        "seconds": time.monotonic() - start,
        "interrupted": bool(interrupt_bytes),
    }


def setting(name, default, low=1, high=4096):
    value = int(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")
    return value


def provision():
    for node in NODES:
        print(node + ": installing pinned offline ZFS/fio packages", flush=True)
        scp = [
            "scp",
            "-q",
            "-i",
            "/keys/id_ed25519",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-P",
            "2222",
        ]
        subprocess.run(
            scp
            + [
                "/opt/packages.tar.gz",
                "/opt/lab/app/observe.py",
                "lab@" + node + ":/tmp/",
            ],
            check=True,
            timeout=180,
        )
        ssh(
            node,
            "mkdir -p /opt/zfslab; cp /tmp/observe.py /opt/zfslab/observe.py; mkdir -p /tmp/zfslab-packages; tar xzf /tmp/packages.tar.gz -C /tmp/zfslab-packages; dpkg --install /tmp/zfslab-packages/*.deb; printf 268435456 > /sys/module/zfs/parameters/zfs_arc_max",
            timeout=600,
        )
        print(node + ": storage runtime ready", flush=True)
    save(
        "versions",
        {node: ssh(node, "uname -r; zfs version; fio --version") for node in NODES},
    )


def device(index):
    if not 0 <= index < 6:
        raise ValueError("unknown owned virtual disk")
    return f"/dev/disk/by-id/virtio-zfslab-d{index}"


def reset(node, layout):
    count = 2 if layout == "mirror" else 4
    disks = [device(i) for i in range(count)]
    archive = evidence.archive_pool(node, RESULTS, ssh_args, ssh)
    if archive:
        print("Preserved prior pool: " + archive["sha256"], flush=True)
    # Only explicitly independent cases recycle pools, after native archival.
    ssh(
        node,
        "if test -f /var/lib/zfslab-exported; then if ! zpool list zfslab >/dev/null 2>&1; then zpool import -N -d /dev/disk/by-id zfslab; fi; rm /var/lib/zfslab-exported; fi; if zpool list zfslab >/dev/null 2>&1; then zpool destroy -f zfslab; fi; udevadm settle; printf 268435456 > /sys/module/zfs/parameters/zfs_arc_max",
    )

    def create(paths):
        return shlex.join(
            [
                "zpool",
                "create",
                "-f",
                "-o",
                "ashift=12",
                "-O",
                "compression=lz4",
                "-O",
                "atime=off",
                "-O",
                "recordsize=128K",
                "-O",
                "mountpoint=/srv/zfslab",
                "zfslab",
                layout,
            ]
            + paths
        )

    try:
        ssh(node, create(disks))
    except RuntimeError as error:
        if "one or more devices is currently unavailable" not in str(error):
            raise
        imported = ssh(
            node,
            "if zpool list zfslab >/dev/null 2>&1; then echo exists; else echo absent; fi",
        )
        if imported != "absent":
            raise RuntimeError(
                "create failed but pool exists; refusing another creation"
            ) from error
        # 2.2 waits for /dev/vdX1, which can precede udev's persistent -part1 link.
        # The failed empty-pool creation has already written GPT; do not relabel again.
        partitions = [path + "-part1" for path in disks]
        ssh(
            node,
            "udevadm settle --timeout=60; "
            + "; ".join("test -b " + shlex.quote(path) for path in partitions),
        )
        print(
            f"{layout}: retry empty-pool creation with settled partition paths",
            flush=True,
        )
        ssh(node, create(partitions))


def state(node):
    fields = ssh(node, "zpool list -Hp -o size,alloc,free,health zfslab").split()
    return {
        "size_bytes": int(fields[0]),
        "allocated_bytes": int(fields[1]),
        "free_bytes": int(fields[2]),
        "health": fields[3],
        "status": ssh(node, "zpool status -P zfslab"),
    }


def healthy(node):
    value = state(node)
    if value["health"] != "ONLINE" or "No known data errors" not in value["status"]:
        raise RuntimeError(f"unhealthy pool: {value}")
    return value


def fio_command(filename, mib, verify_only=False, rate=0):
    args = [
        "fio",
        "--name=payload",
        "--filename=" + filename,
        "--size=" + str(mib * 1024**2),
        "--rw=write",
        "--bs=131072",
        "--ioengine=sync",
        "--iodepth=1",
        "--numjobs=1",
        "--thread=1",
        "--direct=0",
        "--fallocate=none",
        "--refill_buffers=1",
        "--buffer_compress_percentage=0",
        "--randseed=20261114",
        "--verify=crc32c",
        "--verify_fatal=1",
        "--verify_dump=0",
        "--do_verify=" + ("1" if verify_only else "0"),
        "--end_fsync=1",
        "--fsync=8",
        "--output-format=json",
    ]
    if verify_only:
        args += ["--verify_only=1"]
    if rate:
        args += ["--rate=" + str(rate * 1024**2)]
    return shlex.join(args)


def checked_fio(value, mib, verification=False):
    if not value.get("jobs") or any(j["error"] for j in value["jobs"]):
        raise RuntimeError(f"fio failed: {value}")
    direction = "read" if verification else "write"
    if sum(j[direction]["io_bytes"] for j in value["jobs"]) != mib * 1024**2:
        raise RuntimeError("fio did not process the complete expected byte range")
    return value


def fio_file(node, filename, mib, verification=False):
    return checked_fio(
        json.loads(ssh(node, fio_command(filename, mib, verification), timeout=600)),
        mib,
        verification,
    )


def file_info(node, filename):
    # Paths are constants within the owned dataset, never caller-provided devices.
    value = ssh(
        node,
        "stat -c %s " + shlex.quote(filename) + "; sha256sum " + shlex.quote(filename),
        timeout=300,
    ).splitlines()
    return {"bytes": int(value[0]), "sha256": value[1].split()[0]}


def native_action(case, *arguments):
    command = shlex.join(["python3", "/opt/zfslab/observe.py", "action", *arguments])
    started = time.time_ns()
    result = json.loads(ssh("source", command, timeout=600))
    result["controller_started_ns"] = started
    result["controller_ended_ns"] = time.time_ns()
    evidence.save(case / ("action-" + uuid.uuid4().hex + ".json"), result)
    if result["exit_code"]:
        raise RuntimeError("native operation failed: " + result["stderr"])
    return result


def fault_test(layout):
    mib = setting("DATA_MIB", 512, 256, 2048)
    rate = setting("WRITE_MIB_S", 4, 1, 64)
    if mib / rate < 60:
        raise ValueError(
            "DATA_MIB / WRITE_MIB_S must allow at least 60 seconds of active writes"
        )
    reset("source", layout)
    ssh("source", "zfs create zfslab/data")
    before = healthy("source")
    case = RESULTS / "cases" / uuid.uuid4().hex
    case.mkdir(parents=True)
    evidence.save(
        case / "config.json",
        {
            "layout": layout,
            "data_mib": mib,
            "write_mib_s": rate,
            "seed": 20261114,
            "created_ns": time.time_ns(),
            "scope": "Independent immutable reads with native fio writes under member faults and resilver. TCG/shared-host diagnostic, not capacity.",
        },
    )
    immutable = "/srv/zfslab/data/immutable.bin"
    fio_file("source", immutable, 64)
    expected = file_info("source", immutable)
    evidence.save(case / "immutable-ack.json", expected)
    reader, process = None, None
    try:
        reader = evidence.Reader("source", immutable, expected, case, ssh_args, ssh)
        reader.progress("baseline")
        phases = {"baseline": time.time_ns()}
        filename = "/srv/zfslab/data/payload.bin"
        process = subprocess.Popen(
            ssh_args("source", fio_command(filename, mib, rate=rate)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def growth(minimum):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                size = int(
                    ssh("source", f"test ! -f {filename} || stat -c %s {filename}")
                    or "0"
                )
                if process.poll() is not None:
                    raise RuntimeError(
                        "fio completed before live fault/replacement; increase DATA_MIB or lower WRITE_MIB_S"
                    )
                if size >= minimum:
                    return size
                time.sleep(0.2)
            raise RuntimeError("no write progress while degraded")

        growth(16 * 1024**2)
        faults = 1 if layout == "mirror" else 2
        phases["fault-injection"] = time.time_ns()
        for index in range(faults):
            native_action(case, "zpool", "offline", "-f", "zfslab", device(index))
        degraded = state("source")
        if (
            degraded["health"] != "DEGRADED"
            or degraded["status"].count("FAULTED") != faults
        ):
            raise RuntimeError("expected degraded layout was not observed")
        phases["degraded"] = time.time_ns()
        reader.phase("degraded")
        reader.progress("degraded")
        first = int(ssh("source", "stat -c %s " + filename))
        last = growth(first + 8 * 1024**2)
        print(
            f"{layout}: {faults} faulted members; writes progressed by {last - first} bytes",
            flush=True,
        )
        phases["replacement"] = time.time_ns()
        reader.phase("replacement")
        reader.progress("replacement")
        replacements = []
        start = time.monotonic()
        for index in range(faults):
            replacement = 2 if layout == "mirror" else index + 4
            action = native_action(
                case,
                "zpool",
                "replace",
                "-w",
                "zfslab",
                device(index),
                device(replacement),
            )
            replacements.append(action)
            reader.wait_overlap(
                action["started_monotonic_ns"], action["ended_monotonic_ns"]
            )
            if process.poll() is not None:
                raise RuntimeError(
                    "writer exhausted input before native repair completed"
                )
        replaced = time.monotonic() - start
        phases["recovered"] = time.time_ns()
        reader.phase("repaired")
        reader.progress("repaired")
        stdout, stderr = process.communicate(timeout=600)
        if process.returncode:
            raise RuntimeError(f"fio write failed: {stderr}\n{stdout[-1000:]}")
        written = checked_fio(json.loads(stdout), mib)
        evidence.save(case / "write-completion.json", written)
        verified = fio_file("source", filename, mib, True)
        start = time.monotonic()
        native_action(case, "zpool", "scrub", "-w", "zfslab")
        scrub_seconds = time.monotonic() - start
        after = healthy("source")
        reader.phase("after-scrub")
        reader.progress("after-scrub")
        reader.stop()
        result = {
            "data_mib": mib,
            "phase_boundaries_ns": {**phases, "finished": time.time_ns()},
            "phase_clock": "Docker VM UTC shared by controller and native container resource probes; separate guest monotonic clock used only for exact read/action overlap.",
            "faulted_members": faults,
            "before": before,
            "degraded": degraded,
            "after": after,
            "bytes_progressed_while_degraded": last - first,
            "replacement_seconds": replaced,
            "scrub_seconds": scrub_seconds,
            "file": file_info("source", filename),
            "write_fio": written,
            "verification_fio": verified,
            "immutable": expected,
            "read_summary": evidence.summarize_reads(reader.records),
            "replacement_native_intervals": replacements,
            "compression": ssh(
                "source",
                "zfs get -Hp -o name,property,value used,logicalused,compressratio zfslab zfslab/data",
            ),
            "read_history": str(case / "reader.jsonl"),
            "case": str(case),
        }
        evidence.save(case / "result.json", result)
        save(layout, result)
    except BaseException as exception:
        evidence.save(
            case / "failure.json", {"error": str(exception), "ended_ns": time.time_ns()}
        )
        raise
    finally:
        original_failure = sys.exc_info()[0] is not None
        cleanup_errors = []
        try:
            if process is not None and process.poll() is None:
                ssh("source", "pkill -x fio || true")
                process.terminate()
                process.communicate(timeout=30)
        except BaseException as exception:
            cleanup_errors.append({"actor": "fio", "error": str(exception)})
        try:
            if reader is not None and not reader.stopped:
                reader.stop()
        except BaseException as exception:
            cleanup_errors.append({"actor": "reader", "error": str(exception)})
        if cleanup_errors:
            evidence.save(
                case / "cleanup-failure.json",
                {"errors": cleanup_errors, "ended_ns": time.time_ns()},
            )
            if not original_failure:
                raise RuntimeError(
                    "background actor cleanup failed; preserve native storage"
                )


def replication_test():
    mib = setting("DATA_MIB", 512, 256, 2048)
    for node in NODES:
        reset(node, "mirror")
    ssh("source", "zfs create zfslab/data")
    fio_file("source", "/srv/zfslab/data/payload.bin", mib)
    baseline = file_info("source", "/srv/zfslab/data/payload.bin")
    ssh("source", "zfs snapshot zfslab/data@baseline")
    full = replicate("source", "target", "baseline")
    fio_file("source", "/srv/zfslab/data/delta.bin", 64)
    delta = file_info("source", "/srv/zfslab/data/delta.bin")
    ssh("source", "zfs snapshot zfslab/data@next")
    interrupted = replicate(
        "source", "target", "next", "baseline", interrupt_bytes=65536
    )
    token = ssh("target", "zfs get -H -o value receive_resume_token zfslab/data")
    if not token or token == "-":
        raise RuntimeError("no resumable receive token")
    resumed = replicate("source", "target", "next", resume_token=token)
    ssh("target", "zfs clone -o readonly=on zfslab/data@next zfslab/readcopy")
    clone = {
        name: file_info("target", "/srv/zfslab/readcopy/" + name + ".bin")
        for name in ["payload", "delta"]
    }
    if clone != {"payload": baseline, "delta": delta}:
        raise RuntimeError("received clone differs from source")
    fio_file("source", "/srv/zfslab/data/tail.bin", 64)
    # Administrative fencing: no source pool remains mounted or writable.
    ssh("source", "zpool export zfslab")
    if (
        ssh(
            "source",
            "if zpool list zfslab >/dev/null 2>&1; then echo imported; else echo fenced; fi",
        )
        != "fenced"
    ):
        raise RuntimeError("source pool was not fenced")
    ssh("source", "touch /var/lib/zfslab-exported")
    start = time.monotonic()
    ssh(
        "target",
        "zfs destroy zfslab/readcopy; zfs set readonly=off zfslab/data; zfs mount zfslab/data; test ! -f /srv/zfslab/data/tail.bin",
    )
    for name, size in [("payload", mib), ("delta", 64)]:
        fio_file("target", "/srv/zfslab/data/" + name + ".bin", size, True)
    fio_file("target", "/srv/zfslab/data/promoted.bin", 64)
    fio_file("target", "/srv/zfslab/data/promoted.bin", 64, True)
    save(
        "replication",
        {
            "baseline": baseline,
            "delta": delta,
            "clone": clone,
            "full_stream": full,
            "interrupted": interrupted,
            "resumed": resumed,
            "source_pool_exported": True,
            "replicated_bytes_missing": 0,
            "unreplicated_tail_absent_bytes": 64 * 1024**2,
            "new_promoted_bytes_verified": 64 * 1024**2,
            "promotion_verification_and_write_seconds": time.monotonic() - start,
        },
    )


def benchmark():
    mib = setting("BENCH_MIB", 256, 64, 1024)
    seconds = setting("BENCH_SECONDS", 10, 5, 60)
    for layout in ["mirror", "raidz2"]:
        reset("source", layout)
        ssh("source", "zfs create zfslab/data")
        base = [
            "fio",
            "--name=bench",
            "--filename=/srv/zfslab/data/benchmark.bin",
            "--size=" + str(mib * 1024**2),
            "--ioengine=sync",
            "--iodepth=1",
            "--numjobs=1",
            "--thread=1",
            "--direct=0",
            "--refill_buffers=1",
            "--buffer_compress_percentage=0",
            "--randseed=20261114",
            "--end_fsync=1",
            "--output-format=json",
        ]
        reports = {}
        for name, options in [
            ("sequential-write", ["--rw=write", "--bs=131072"]),
            ("sequential-read", ["--rw=read", "--bs=131072"]),
            (
                "random-70read",
                [
                    "--rw=randrw",
                    "--rwmixread=70",
                    "--bs=4096",
                    "--time_based=1",
                    "--runtime=" + str(seconds),
                ],
            ),
        ]:
            value = json.loads(ssh("source", shlex.join(base + options), timeout=300))
            if any(j["error"] for j in value["jobs"]):
                raise RuntimeError("fio benchmark failed")
            reports[name] = value
        fio_file("source", "/srv/zfslab/data/verification.bin", 64)
        fio_file("source", "/srv/zfslab/data/verification.bin", 64, True)
        save(
            "benchmark-" + layout,
            {
                "mib": mib,
                "post_benchmark_verified_bytes": 64 * 1024**2,
                "random_seconds": seconds,
                "pool": healthy("source"),
                "fio": reports,
                "cache": "buffered; warm ARC/OS caches; no claim of physical disk IOPS; sequential write includes final fsync",
            },
        )


def main():
    action = sys.argv[1]
    if action == "up":
        provision()
    elif action == "test":
        selected = sys.argv[2] if len(sys.argv) > 2 else "all"
        if selected not in ["all", "mirror", "raidz2", "replication"]:
            raise ValueError("SETUP must be all, mirror, raidz2 or replication")
        for layout in (
            ["mirror", "raidz2", "replication"] if selected == "all" else [selected]
        ):
            print("Starting " + layout, flush=True)
            if layout == "replication":
                replication_test()
            else:
                fault_test(layout)
    elif action == "archive-pools":
        for node in NODES:
            evidence.archive_pool(node, RESULTS, ssh_args, ssh)
    elif action == "benchmark":
        benchmark()
    elif action == "status":
        for node in NODES:
            print(
                node,
                ssh(
                    node,
                    "if zpool list zfslab >/dev/null 2>&1; then zpool status zfslab; zfs list -t filesystem,snapshot; else echo no-imported-example-pool; fi",
                ),
            )
    elif action == "down":
        for node in NODES:
            ssh(
                node,
                "if zpool list zfslab >/dev/null 2>&1; then zpool export zfslab; touch /var/lib/zfslab-exported; fi",
            )
    else:
        raise ValueError(
            "usage: up|test [all|mirror|raidz2|replication]|benchmark|status|down"
        )


if __name__ == "__main__":
    main()
