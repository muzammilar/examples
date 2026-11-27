"""Independent body/replica checks and a live native-fencing failover case."""

import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import lab


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def events(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def cpp(mode, run, directory):
    result = lab.command(
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "RUN_ID=" + run,
        "files-writer",
        mode,
        check=False,
        timeout=180,
    )
    target = directory / (mode + "-" + str(time.time_ns()) + ".log")
    target.write_text(result.stdout + result.stderr)
    records = events(result.stdout)
    expected = hashlib.sha256(
        (lab.ROOT / "files-workload/main.cpp").read_bytes()
    ).hexdigest()
    if result.returncode or not any(
        r.get("source_sha256") == expected for r in records
    ):
        raise RuntimeError(
            "Native client failed/source mismatch; preserve " + str(target)
        )
    return records


def witness(run):
    raw = lab.execute("tools", "cat", "/witness/" + run + "/journal.jsonl").stdout
    return raw, [json.loads(line) for line in raw.splitlines() if line.endswith("}")]


def control(run, name, value):
    if not re.fullmatch("[0-9a-f]{32}", run) or name not in (
        "phase",
        "stop",
        "stop-reader",
    ):
        raise ValueError("Unexpected witness control path")
    code = (
        'from pathlib import Path; import os,sys; p=Path("/witness")/sys.argv[1]/sys.argv[2]; '
        't=p.with_name(p.name+".tmp"); f=t.open("w"); f.write(sys.argv[3]); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p)'
    )
    lab.execute("witness-control", "python3", "-c", code, run, name, value)


def fixture():
    return json.loads((lab.ROOT / "results/fixture.json").read_text())


def audit(directory):
    cases = [
        read
        for p in (lab.ROOT / "results/cases").glob("*/case.json")
        if (read := json.loads(p.read_text()))["fixture"] == fixture()["id"]
    ]
    expected = {}
    for case in cases:
        records = cpp("verify", case["run_id"], directory)
        if not any(
            r.get("event") == "verified"
            and r.get("acknowledged_missing") == 0
            and r.get("wrong") == 0
            for r in records
        ):
            raise ValueError("No complete ACK verification result")
        raw, history = witness(case["run_id"])
        (directory / (case["run_id"] + "-journal.jsonl")).write_text(raw)
        for item in history:
            if item["event"] == "ack":
                expected[item["path"]] = item
    native = lab.execute(
        "tools", "hdfs", "fsck", "/lab/objects", "-files", "-blocks", "-locations"
    )
    (directory / "fsck.txt").write_text(native.stdout + native.stderr)
    if "Status: HEALTHY" not in native.stdout:
        raise ValueError("Native fsck unhealthy")
    files = {}
    current = None
    for line in native.stdout.splitlines():
        match = re.match(r"(/lab/objects/\S+)\s+(\d+) bytes", line)
        if match:
            current = match[1]
            files[current] = {"bytes": int(match[2]), "blocks": []}
        elif line.startswith("/"):
            current = None
        block = re.search(r"\b(blk_-?\d+)_\d+\s+len=(\d+)\s+Live_repl=(\d+)", line)
        if block and current:
            if int(block[3]) != 3:
                raise ValueError(
                    "Published block does not have three native live copies"
                )
            files[current]["blocks"].append({"id": block[1], "bytes": int(block[2])})
    if not set(expected).issubset(files):
        raise ValueError("Acknowledged paths absent from native namespace")
    # Unacknowledged final paths are checked by C++ against pre-mutation attempts.
    # Match their expected bodies too before including them in physical checks.
    for case in cases:
        _, history = witness(case["run_id"])
        for item in history:
            if item["event"] == "attempt" and item["path"] in files:
                expected.setdefault(item["path"], item)
    if set(files) != set(expected):
        raise ValueError("Unwitnessed published paths or unknown fsck format")
    for path, info in files.items():
        if not info["blocks"] or info["bytes"] != expected[path]["bytes"]:
            raise ValueError("Incomplete native file/block information")
    code = r"""import hashlib,json,re
from pathlib import Path
files=FILES
blocks={p.name:p for p in Path('/data/data/current').rglob('blk_*') if p.is_file() and 'finalized' in p.parts and re.fullmatch(r'blk_-?\d+',p.name)}
result={}
for name,info in files.items():
    digest=hashlib.sha256();count=0
    for block in info['blocks']:
        p=blocks[block['id']]
        if p.stat().st_size!=block['bytes']:raise ValueError('Wrong finalized block size')
        with p.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1048576),b''):
                digest.update(chunk);count+=len(chunk)
    result[name]={'sha256':digest.hexdigest(),'bytes':count}
print(json.dumps(result))""".replace("FILES", repr(files))
    copies = {}
    for node in ("dn1", "dn2", "dn3"):
        bodies = json.loads(lab.execute(node, "python3", "-c", code).stdout)
        for path, value in bodies.items():
            if (
                value["sha256"] != expected[path]["sha256"]
                or value["bytes"] != expected[path]["bytes"]
            ):
                raise ValueError("Wrong physical replica bytes on " + node)
        copies[node] = bodies
    save(
        directory / "replicas.json",
        {
            "published_files": len(files),
            "blocks": sum(len(v["blocks"]) for v in files.values()),
            "logical_body_bytes": sum(v["bytes"] for v in files.values()),
            "copies": copies,
            "all_three_physical_hashes_match": True,
        },
    )


def baseline(directory):
    index = lab.ROOT / "results/fixture.json"
    if index.exists():
        raise RuntimeError("Fixture exists; verify/recover it instead of reseeding")
    data = {
        "id": uuid.uuid4().hex,
        "namespace": lab.execute("nn1", "cat", "/data/name/current/VERSION").stdout,
    }
    save(index, data)
    run = uuid.uuid4().hex
    case = lab.ROOT / "results/cases" / run
    case.mkdir(parents=True)
    save(case / "case.json", {"fixture": data["id"], "run_id": run, "mode": "seed"})
    cpp("seed", run, directory)
    audit(directory)
    save(
        directory / "result.json",
        {"baseline": "passed", "seed_bytes": 67108864, "run_id": run},
    )


def logs(identifier):
    return subprocess.check_output(
        ["docker", "logs", identifier], text=True, stderr=subprocess.STDOUT
    )


def failover(directory):
    initial = directory / "before"
    initial.mkdir()
    audit(initial)
    before = lab.wait_roles()
    old = next(n for n, v in before.items() if v["State"] == "active")
    new = "nn2" if old == "nn1" else "nn1"
    run = uuid.uuid4().hex
    case = lab.ROOT / "results/cases" / run
    case.mkdir(parents=True)
    save(
        case / "case.json",
        {"fixture": fixture()["id"], "run_id": run, "mode": "pause-fence"},
    )
    actors = {}
    recovered = False
    try:
        for service, mode in (("files-writer", "writer"), ("files-reader", "reader")):
            name = "hdfs-ha-" + mode + "-" + run[:12]
            identifier = lab.command(
                "run",
                "--no-deps",
                "-d",
                "--name",
                name,
                "-e",
                "RUN_ID=" + run,
                "-e",
                "DURATION_S=120",
                service,
                mode,
            ).stdout.strip()
            actors[mode] = identifier
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                _, history = witness(run)
            except RuntimeError:
                history = []
            reads = events(logs(actors["reader"]))
            if (
                sum(r["event"] == "ack" for r in history) >= 5
                and sum(r.get("result") == "ok" for r in reads) >= 3
            ):
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("Live writer/reader did not establish progress")
        control(run, "phase", "fault")
        identity = json.loads(
            subprocess.check_output(
                ["docker", "inspect", lab.command("ps", "-q", old).stdout.strip()],
                text=True,
            )
        )[0]
        if (
            identity["Config"]["Labels"].get("com.docker.compose.project")
            != "hadoop-metadata-ha"
        ):
            raise ValueError("Wrong fault ownership")
        pid = lab.execute(
            old, "cat", "/control/hadoop-hadoop-namenode.pid"
        ).stdout.strip()
        if not pid.isdecimal() or int(pid) <= 1:
            raise ValueError("Unexpected native daemon PID")
        cmdline = lab.execute(old, "cat", "/proc/" + pid + "/cmdline").stdout
        if "org.apache.hadoop.hdfs.server.namenode.NameNode" not in cmdline:
            raise ValueError("PID does not identify NameNode")
        save(
            directory / "before-injection.json",
            {
                "roles": before,
                "node": old,
                "pid": pid,
                "container_id": identity["Id"],
                "cmdline": cmdline,
            },
        )
        fence_since = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
        ).isoformat()
        started = time.monotonic()
        lab.execute(old, "kill", "-STOP", pid)
        state = lab.execute(old, "cat", "/proc/" + pid + "/status").stdout
        if not re.search(r"^State:\s+T", state, re.M):
            raise ValueError("Native process not observed stopped")
        (directory / "paused-process.txt").write_text(state)
        deadline = time.monotonic() + 60
        with (directory / "roles.jsonl").open("x") as observations:
            while time.monotonic() < deadline:
                roles = lab.states()
                observations.write(
                    json.dumps(
                        {"elapsed_s": time.monotonic() - started, "roles": roles}
                    )
                    + "\n"
                )
                observations.flush()
                if (
                    roles[new].get("State") == "active"
                    and roles[old].get("State") != "active"
                ):
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError(
                    "Standby did not become active within the declared 60s bound"
                )
        fence_logs = lab.command(
            "logs",
            "--since",
            fence_since,
            "--no-color",
            "--no-log-prefix",
            "zkfc" + new[-1],
        ).stdout
        (directory / "fencing.log").write_text(fence_logs)
        proofs = [
            json.loads(line.split("OPENSSH_FENCE ", 1)[1])
            for line in fence_logs.splitlines()
            if "OPENSSH_FENCE {" in line
        ]
        if not any(
            p.get("host") == old
            and p.get("port") == 9000
            and pid in p.get("pids", [])
            and p.get("listener") == "refused"
            for p in proofs
        ):
            raise ValueError(
                "No native SSH kill proof; leader switch alone is insufficient"
            )
        old_pid = lab.execute(old, "test", "-e", "/proc/" + pid, check=False)
        if old_pid.returncode == 0:
            raise ValueError("Old NameNode process still exists after fencing")
        failover_seconds = time.monotonic() - started
        _, history = witness(run)
        acks_before = sum(r["event"] == "ack" for r in history)
        control(run, "phase", "recovery")
        lab.execute(
            old,
            "hdfs",
            "--config",
            "/tmp/hadoop-role-conf",
            "--daemon",
            "start",
            "namenode",
        )
        roles = lab.wait_roles(expected_active=new)
        recovered = True
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, history = witness(run)
            if sum(r["event"] == "ack" for r in history) >= acks_before + 5:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("Writes did not resume after failover/rejoin")
        control(run, "stop", "stop")
        writer_status = subprocess.check_output(
            ["docker", "wait", actors["writer"]], text=True, timeout=30
        ).strip()
        control(run, "stop-reader", "stop")
        reader_status = subprocess.check_output(
            ["docker", "wait", actors["reader"]], text=True, timeout=30
        ).strip()
        if writer_status != "0" or reader_status != "0":
            raise ValueError("A live workload actor failed")
        audit(directory)
        _, history = witness(run)
        save(
            directory / "result.json",
            {
                "failover": "passed",
                "old_active": old,
                "new_active": new,
                "observed_failover_and_fencing_s": failover_seconds,
                "input_reloaded": False,
                "roles_after_rejoin": roles,
                "acks_after": sum(r["event"] == "ack" for r in history),
            },
        )
    finally:
        for mode, identifier in actors.items():
            info = json.loads(
                subprocess.check_output(["docker", "inspect", identifier], text=True)
            )[0]
            if (
                info["Config"]["Labels"].get("com.docker.compose.project")
                != "hadoop-metadata-ha"
            ):
                raise ValueError("Unexpected actor ownership")
            if info["State"]["Running"]:
                subprocess.run(
                    ["docker", "stop", "--time", "20", identifier], check=True
                )
            (directory / (mode + ".log")).write_text(logs(identifier))
            save(
                directory / (mode + "-container.json"),
                json.loads(
                    subprocess.check_output(
                        ["docker", "inspect", identifier], text=True
                    )
                )[0],
            )
            subprocess.run(["docker", "rm", identifier], check=True)
        if not recovered:
            # Remove STOP only if the same witnessed process still exists; never signal a recycled PID.
            try:
                current = lab.execute(
                    old, "cat", "/proc/" + pid + "/cmdline", check=False
                )
                if current.returncode == 0 and current.stdout == cmdline:
                    lab.execute(old, "kill", "-CONT", pid)
            except (UnboundLocalError, RuntimeError):
                pass


def main():
    mode = sys.argv[1]
    if mode not in ("baseline", "verify", "failover"):
        raise ValueError(mode)
    directory = lab.ROOT / "results" / (mode + "-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    lab.wait_roles()
    if mode != "baseline":
        keys = lambda raw: dict(
            line.split("=", 1)
            for line in raw.splitlines()
            if "=" in line and not line.startswith("#")
        )
        before = keys(fixture()["namespace"])
        now = keys(lab.execute("nn1", "cat", "/data/name/current/VERSION").stdout)
        if any(
            before[k] != now[k] for k in ("clusterID", "namespaceID", "blockpoolID")
        ):
            raise ValueError("Witness belongs to a different namespace; never reseed")
    if mode == "baseline":
        baseline(directory)
    elif mode == "verify":
        audit(directory)
    else:
        failover(directory)
    print(json.dumps({"mode": mode, "passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
