"""Example-owned histories; never overwrite a prior test or recycle unarchived data."""

import hashlib
import json
import os
import re
import shlex
import statistics
import shutil
import math
import subprocess
import threading
import time
import uuid


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Reader:
    def __init__(self, node, filename, expected, root, ssh_args, ssh):
        self.node, self.root, self.ssh_args, self.ssh = node, root, ssh_args, ssh
        self.control = "/run/zfslab-reader-" + uuid.uuid4().hex
        self.records, self.error, self.stopped = [], None, False
        self.phase("baseline")
        command = "python3 /opt/zfslab/observe.py read "
        command += shlex.join([filename, json.dumps(expected), self.control])
        self.errors = (root / "reader.stderr").open("x")
        self.process = subprocess.Popen(
            ssh_args(node, command),
            stdout=subprocess.PIPE,
            stderr=self.errors,
            text=True,
        )
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def collect(self):
        try:
            with (self.root / "reader.jsonl").open("x") as output:
                for line in self.process.stdout:
                    value = json.loads(line)
                    value["received_ns"] = time.time_ns()
                    output.write(json.dumps(value) + "\n")
                    output.flush()
                    os.fsync(output.fileno())
                    self.records.append(value)
        except Exception as exception:
            self.error = exception

    def phase(self, name):
        self.ssh(
            self.node,
            "printf %s " + shlex.quote(name) + " > " + self.control + ".phase",
        )

    def progress(self, phase, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error:
                raise RuntimeError("reader journal failed") from self.error
            if self.process.poll() is not None:
                raise RuntimeError("independent reader exited before recovery finished")
            if any(row.get("result") in ("fail", "mismatch") for row in self.records):
                raise RuntimeError("immutable reader observed failed or incorrect data")
            if any(
                row.get("phase_start") == row.get("phase_end") == phase
                and row.get("result") == "ok"
                for row in self.records
            ):
                return
            time.sleep(0.05)
        raise RuntimeError("no successful independent read during " + phase)

    def wait_overlap(self, started_ns, ended_ns, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if reads_within(self.records, started_ns, ended_ns):
                return
            if self.error:
                raise RuntimeError("reader journal failed") from self.error
            # This stream is ordered: once a later invocation is durable there
            # cannot still be an earlier in-window result waiting in transport.
            if any(
                r.get("kind") == "read" and r["started_monotonic_ns"] >= ended_ns
                for r in self.records
            ):
                break
            if self.process.poll() is not None:
                raise RuntimeError("reader exited before overlap evidence was flushed")
            time.sleep(0.05)
        raise RuntimeError(
            "no complete independent read inside native replacement interval"
        )

    def stop(self):
        if self.stopped:
            return
        self.ssh(self.node, "touch " + self.control + ".stop")
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=15)
            raise RuntimeError("independent reader did not stop")
        finally:
            self.thread.join(5)
            self.errors.close()
        self.stopped = True
        if self.thread.is_alive() or self.error or self.process.returncode:
            raise RuntimeError("reader failed to flush its history") from self.error
        if not self.records or self.records[-1].get("kind") != "reader-stopped":
            raise RuntimeError("reader termination was not acknowledged")
        if any(row.get("result") in ("fail", "mismatch") for row in self.records):
            raise RuntimeError("immutable reader observed failed or incorrect data")


def reads_within(records, started_ns, ended_ns):
    return [
        r
        for r in records
        if r.get("kind") == "read"
        and r.get("result") == "ok"
        and started_ns
        <= r["started_monotonic_ns"]
        <= r["ended_monotonic_ns"]
        <= ended_ns
    ]


def summarize_reads(records):
    output = {}
    for phase in sorted({r["phase_start"] for r in records if r.get("kind") == "read"}):
        selected = [
            r
            for r in records
            if r.get("kind") == "read" and r["phase_start"] == r["phase_end"] == phase
        ]
        values = sorted(r["elapsed_ns"] / 1e6 for r in selected if r["result"] == "ok")
        output[phase] = {
            "ok": len(values),
            "fail": sum(r["result"] == "fail" for r in selected),
            "mismatch": sum(r["result"] == "mismatch" for r in selected),
            "mean_ms": statistics.mean(values) if values else None,
            "p50_ms": statistics.median(values) if values else None,
            "p99_ms": values[math.ceil(len(values) * 0.99) - 1] if values else None,
            "scope": "Complete 64 MiB SHA256 read, including hashing; shared guest monotonic clock bounds native-action overlap. Small-sample p99 is effectively maximum.",
        }
    return output


def stream_complete(completed, size, expected_snapshots=()):
    # zstream 2.2 can print a checksum error and still exit zero. Check its
    # stderr, complete byte count and BEGIN/END balance, not exit status alone.
    output = completed.stdout.decode(errors="replace")
    begin = re.search(r"Total DRR_BEGIN records = (\d+)", output)
    end = re.search(r"Total DRR_END records = (\d+)", output)
    length = re.search(r"Total stream length = (\d+)", output)
    header_types = list(map(int, re.findall(r"hdrtype = (\d+)", output)))
    # libzfs_sendrecv sends a separate terminal END for each compound package.
    # Every ordinary substream remains BEGIN/END-balanced.
    compounds = header_types.count(2)
    return (
        completed.returncode == 0
        and not completed.stderr
        and "Expected checksum differs" not in output
        and "Incorrect checksum" not in output
        and begin
        and end
        and length
        and int(begin[1]) > 0
        and len(header_types) == int(begin[1])
        and all(kind in (1, 2) for kind in header_types)
        and int(end[1]) == int(begin[1]) + compounds
        and int(length[1]) == size
        and set(expected_snapshots) <= set(re.findall(r"toname = ([^\s]+)", output))
    )


def archive_pool(node, results, ssh_args, ssh):
    """Export a native recursive snapshot before replacing an existing lab pool."""
    # A previously exported owned pool is imported without mounting its datasets.
    ssh(
        node,
        "if test -f /var/lib/zfslab-exported; then zpool import -N -d /dev/disk/by-id zfslab; rm /var/lib/zfslab-exported; fi",
    )
    names = ssh(node, "zpool list -H -o name").splitlines()
    if "zfslab" not in names:
        return None
    ssh(
        node,
        "if pgrep -x fio >/dev/null; then echo 'fio remains active' >&2; exit 11; fi; "
        "if pgrep -f '^python3 /opt/zfslab/observe.py read' >/dev/null; then echo 'reader remains active' >&2; exit 12; fi; "
        'for ds in $(zfs list -H -r -t filesystem -o name zfslab); do zfs set readonly=on "$ds"; done; zpool sync zfslab',
    )
    if shutil.disk_usage(results).free < 4 * 1024**3:
        raise RuntimeError(
            "need at least 4 GiB free before native pool archival; existing storage preserved"
        )
    root = results / "pool-archives" / uuid.uuid4().hex
    root.mkdir(parents=True)
    snapshot = "evidence_" + root.name
    ssh(node, "zfs snapshot -r zfslab@" + snapshot)
    expected = [
        name
        for name in ssh(node, "zfs list -H -r -t snapshot -o name zfslab").splitlines()
        if name.endswith("@" + snapshot)
    ]
    if not expected:
        raise RuntimeError(
            "native recursive snapshot inventory is empty; preserve original pool"
        )
    stream_path = root / "pool.zfs"
    with stream_path.open("xb") as output:
        completed = subprocess.run(
            ssh_args(node, "zfs send -R zfslab@" + snapshot),
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=600,
            check=False,
        )
        output.flush()
        os.fsync(output.fileno())
    if completed.returncode or completed.stderr:
        save(
            root / "failure.json",
            {"stage": "send", "error": completed.stderr.decode(errors="replace")},
        )
        raise RuntimeError("pool archive failed; existing pool must remain intact")
    with stream_path.open("rb") as stream:
        checked = subprocess.run(
            ssh_args(node, "zstream dump"),
            stdin=stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
            check=False,
        )
    if not stream_complete(checked, stream_path.stat().st_size, expected):
        save(
            root / "failure.json",
            {
                "stage": "native-stream-check",
                "error": checked.stderr.decode(errors="replace"),
                "stdout": checked.stdout.decode(errors="replace"),
                "exit_code": checked.returncode,
                "expected_dataset_snapshots": expected,
            },
        )
        raise RuntimeError("pool archive did not pass native checksum verification")
    with stream_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    proof = {
        "node": node,
        "snapshot": "zfslab@" + snapshot,
        "dataset_snapshots": expected,
        "archive": str(stream_path),
        "bytes": stream_path.stat().st_size,
        "sha256": digest,
        "native_stream_check_exit_code": checked.returncode,
        "native_stream_summary": checked.stdout.decode(errors="replace"),
        "scope": "Native recursive send stream preserved outside both guests before reset; no restore qualification claimed.",
    }
    save(root / "verified.json", proof)
    return proof
