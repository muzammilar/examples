"""Example-owned Vespa lifecycle, preserved inventory histories and native copy proof."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import calendar
import re
import json
import hashlib
import shutil
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

from metrics import cgroup_counters, cgroup_cpu_seconds, summarize
from version_history import History, digest, empty, records

ROOT = Path(__file__).resolve().parent
CONTROLLER_SOURCE = Path(__file__).read_bytes()
MODE = os.environ.get("MODE", "cluster")
if MODE not in {"single", "cluster", "sharded"}:
    raise ValueError("invalid MODE")
BASE = (ROOT / os.environ.get("RESULTS_DIR", "results/production/" + MODE)).resolve()
if not BASE.is_relative_to(ROOT / "results"):
    raise ValueError("RESULTS_DIR must be local results")
PROJECT = "vespa-phased-product-search"
SERVICES = [f"node{i}" for i in range(1 if MODE == "single" else 3)]
if MODE == "sharded":
    SERVICES += [f"content{i}" for i in range(9)]
CONTENT = [f"content{i}" for i in range(9)] if MODE == "sharded" else SERVICES
COMPOSE = ["docker", "compose", "--project-name", PROJECT] + (
    [] if MODE == "single" else ["--profile", MODE]
)
USER = f"{os.getuid()}:{os.getgid()}"


def run(args, *, env=None, timeout=1200):
    result = subprocess.run(
        args, env=env, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        print(result.stdout[-5000:] + result.stderr[-5000:], flush=True)
        result.check_returncode()
    return result.stdout


def env_for(directory):
    return os.environ | {
        "MODE": MODE,
        "RESULTS_DIR": str(directory.relative_to(ROOT)),
        "URLS": ",".join(
            f"http://node{i}:8080" for i in range(1 if MODE == "single" else 3)
        ),
    }


def compose(*args, directory=BASE, timeout=1200):
    return run(COMPOSE + list(args), env=env_for(directory), timeout=timeout)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path):
    return json.loads(path.read_text())


def inspect():
    ids = run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).split()
    values = json.loads(run(["docker", "inspect", *ids])) if ids else []
    result = {}
    for value in values:
        labels = value["Config"]["Labels"]
        if labels.get("com.docker.compose.oneoff", "").lower() == "true":
            continue
        service = labels.get("com.docker.compose.service")
        if service not in SERVICES:
            raise RuntimeError("another MODE or unexpected service owns the project")
        if service in result:
            raise RuntimeError("duplicate native service")
        result[service] = value
    return result


def identity(state):
    return {
        name: {
            "id": v["Id"],
            "started": v["State"]["StartedAt"],
            "image": v["Image"],
            "mounts": v["Mounts"],
        }
        for name, v in state.items()
    }


def process_witness():
    """Witness native daemons, including children that can restart under PID 1."""
    result = {}
    for service in SERVICES:
        listing = compose("exec", "-T", service, "vespa-sentinel-cmd", "list")
        roles = {}
        for line in listing.splitlines():
            match = re.match(r'^(.+?) state=(\S+) .*?pid=(\d+).*?id="([^"]+)"', line)
            if match:
                if match[2] != "RUNNING":
                    raise RuntimeError(
                        "native daemon not running: " + service + "/" + match[4]
                    )
                roles[match[4]] = int(match[3])
        if not roles:
            raise RuntimeError("native daemon inventory is empty")
        processes = {}
        for line in compose(
            "exec",
            "-T",
            "-e",
            "LC_ALL=C",
            "-e",
            "TZ=UTC",
            service,
            "ps",
            "-eo",
            "pid=,ppid=,lstart=,args=",
        ).splitlines():
            fields = line.split(maxsplit=7)
            if len(fields) != 8:
                raise RuntimeError("incomplete native process listing")
            pid, parent = int(fields[0]), int(fields[1])
            started = calendar.timegm(
                time.strptime(" ".join(fields[2:7]), "%a %b %d %H:%M:%S %Y")
            )
            processes[pid] = {
                "pid": pid,
                "parent": parent,
                "started_unix": started,
                "args_sha256": hashlib.sha256(fields[7].encode()).hexdigest(),
            }
            if "vespa-runserver" in fields[7]:
                for role, pidfile in [
                    ("configserver", "configserver.pid"),
                    ("configproxy", "configproxy.pid"),
                    ("sentinel", "sentinel.pid"),
                ]:
                    if "var/run/" + pidfile in fields[7]:
                        roles[role + "-supervisor"] = pid
        for role, pid in list(roles.items()):
            if role.endswith("-supervisor"):
                children = [
                    p for p, value in processes.items() if value["parent"] == pid
                ]
                if not children:
                    raise RuntimeError("native supervisor has no child: " + role)
                for index, child in enumerate(children):
                    roles[role + "-child" + str(index)] = child
        if any(pid not in processes for pid in roles.values()):
            raise RuntimeError("native daemon changed during process snapshot")
        result[service] = {role: processes[pid] for role, pid in roles.items()}
    return result


def resource_counters():
    result = {}
    for service in SERVICES:
        started = time.monotonic()
        values = cgroup_counters(
            compose(
                "exec",
                "-T",
                service,
                "cat",
                "/sys/fs/cgroup/cpu.stat",
                "/sys/fs/cgroup/memory.current",
                "/sys/fs/cgroup/memory.peak",
                "/sys/fs/cgroup/memory.stat",
            )
        )
        result[service] = {
            "time": time.time(),
            "monotonic": time.monotonic(),
            "inspection_seconds": time.monotonic() - started,
            "counters": values,
        }
    return result


def live(processes):
    for role, process in (processes or {}).items():
        if process.poll() is not None:
            raise RuntimeError(role + " stopped before native repair completed")


def job(directory, args, processes=None, timeout=1200):
    name = PROJECT + "-check-" + uuid.uuid4().hex[:10]
    log = directory / ("job-" + uuid.uuid4().hex + ".log")
    with log.open("x") as output:
        process = subprocess.Popen(
            COMPOSE
            + [
                "run",
                "--rm",
                "-T",
                "--name",
                name,
                "--user",
                USER,
                "--entrypoint",
                "python",
                "tools",
                *args,
            ],
            env=env_for(directory),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                live(processes)
                if time.monotonic() > deadline:
                    raise TimeoutError("verification timed out: " + str(args))
                time.sleep(0.25)
            if process.returncode:
                print(log.read_text()[-5000:], flush=True)
                raise RuntimeError("verification failed: " + str(args))
        finally:
            if process.poll() is None:
                # The exact random name belongs to this one-shot job, never a SUT.
                run(["docker", "rm", "-f", name], timeout=30)
                process.wait(timeout=30)
    return log


def history():
    value = empty()
    cases = sorted(
        (BASE / "cases").glob("*/case.json"),
        key=lambda p: (
            read(p)["start_version"],
            read(p).get("created_at", p.stat().st_mtime),
        ),
    )
    for metadata in cases:
        case = metadata.parent
        if read(case / "history.json") != value:
            raise RuntimeError("historical chain changed")
        current = History(value)
        for event in records(case / "writer.jsonl"):
            current.append(event)
        value = current.value()
    return value


def binding():
    fixture = read(BASE / "fixture.json")
    if fixture["status"] != "ready":
        raise RuntimeError("interrupted fixture setup; inspect it before proceeding")
    if (
        fixture.get("corpus_sha256")
        != read(ROOT / "results/corpus/prepared/manifest.json")["sha256"]
    ):
        raise RuntimeError("fixture corpus fingerprint changed")
    if fixture["mode"] != MODE or set(inspect()) != set(SERVICES):
        raise RuntimeError("native fixture topology changed")
    for name, value in inspect().items():
        if (
            value["Id"] != fixture["containers"][name]["id"]
            or value["Mounts"] != fixture["containers"][name]["mounts"]
        ):
            raise RuntimeError("unrecorded native container/storage change")
    return fixture


def native(directory, processes=None):
    job(directory, ["/app/replica_audit.py", "state"], processes)
    return read(directory / "native-state.json")


def all_up(state):
    cluster = state["native"]["state"]
    return all(
        cluster["service"][kind]["node"][str(i)]["state"]["generated"]["state"] == "up"
        for kind in ["storage", "distributor"]
        for i in range(len(CONTENT))
    )


def repair_ready(state):
    if not all_up(state):
        return False
    if (
        state["native"]["state"]
        .get("metrics", {})
        .get("cluster-buckets-out-of-sync-ratio")
        != 0
    ):
        return False
    expected = read(ROOT / "results/corpus/prepared/manifest.json")["parameters"][
        "documents"
    ]
    totals = []
    for service in CONTENT:
        values = {}
        for host in state["metrics"][service]["nodes"]:
            for daemon in host["services"]:
                for entry in daemon.get("metrics", []):
                    values.update(entry.get("values", {}))
        if values.get("vds.idealstate.merge_bucket.pending.average") != 0:
            return False
        total = values.get("content.proton.documentdb.documents.total.last")
        ready = values.get("content.proton.documentdb.documents.ready.last")
        if total is None or total != ready:
            return False
        totals.append(total)
    if MODE == "sharded":
        return all(0 < count < expected for count in totals) and all(
            sum(totals[i : i + 3]) == expected for i in [0, 3, 6]
        )
    return all(count == expected for count in totals)


def wait_native(directory, processes=None, timeout=600):
    deadline = time.monotonic() + timeout
    while True:
        live(processes)
        try:
            value = native(directory, processes)
            if repair_ready(value):
                return value
        except RuntimeError as error:
            print("Native readiness:", error, flush=True)
        if time.monotonic() > deadline:
            raise TimeoutError("native storage/distributor readiness incomplete")
        time.sleep(2)


def proof(directory, floor, processes=None, prefix="native"):
    minimum = (
        8 * 1024**3
        + len(CONTENT)
        * read(ROOT / "results/corpus/prepared/manifest.json")["parameters"][
            "documents"
        ]
        * 384
        * 4
        * 4
    )
    if shutil.disk_usage(ROOT).free < minimum:
        raise RuntimeError(
            "insufficient free host space for bounded native exports; retained evidence is not deleted"
        )
    save(directory / (prefix + "-floor.json"), floor)
    save(directory / "floor.json", floor)
    wait_native(directory, processes)
    n = read(ROOT / "results/corpus/prepared/manifest.json")["parameters"]["documents"]
    ids = directory / (prefix + "-ids.txt")
    ids.write_text("".join(f"id:catalog:product::{i}\n" for i in range(n)))
    fields = "pid,source_id,locale,name,description,brand,color,tenant,stock,price_cents,version,embedding"

    def export(item):
        node, service = item
        args = [
            "timeout",
            "--signal=KILL",
            "600",
            "vespa-get",
            "--cluster",
            "catalog",
            "--replica-override",
            str(node),
            "--fieldset",
            "product:" + fields,
            "--shorttensors",
            "--timeout",
            "3",
        ]
        command = shlex.join(args) + " | gzip -1"
        with (
            ids.open("rb") as source,
            (directory / f"{prefix}-{node}.json.gz").open("xb") as out,
            (directory / f"{prefix}-{node}.stderr").open("x") as errors,
        ):
            process = subprocess.Popen(
                COMPOSE
                + [
                    "exec",
                    "-T",
                    "-e",
                    "LANG=C.UTF-8",
                    "-e",
                    "JAVA_TOOL_OPTIONS=-Xmx128m -Dfile.encoding=UTF-8 -Dsun.stdout.encoding=UTF-8",
                    service,
                    "/bin/bash",
                    "-o",
                    "pipefail",
                    "-c",
                    command,
                ],
                env=env_for(directory),
                stdin=source,
                stdout=out,
                stderr=errors,
            )
            try:
                deadline = time.monotonic() + 630
                while process.poll() is None:
                    live(processes)
                    if time.monotonic() > deadline:
                        raise TimeoutError("native export deadline exceeded")
                    time.sleep(0.25)
                if process.returncode:
                    raise RuntimeError("native export failed: " + service)
            finally:
                if process.poll() is None:
                    # Native timeout fences its own process group by 600 seconds.
                    process.terminate()
                    process.wait(timeout=30)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(export, enumerate(CONTENT)))
    job(directory, ["/app/replica_audit.py", prefix], processes)
    ids.unlink()
    return read(directory / (prefix + "-proof.json"))


def marker(directory, phase):
    with (directory / "phases.jsonl").open("a") as stream:
        stream.write(json.dumps({"phase": phase, "time": time.time()}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def traffic(directory):
    result = {}
    for role in ["writer", "reader"]:
        log = (directory / (role + ".log")).open("x")
        try:
            result[role] = subprocess.Popen(
                COMPOSE
                + [
                    "run",
                    "--rm",
                    "-T",
                    "--name",
                    PROJECT + "-" + role,
                    "--user",
                    USER,
                    "inventory-workload",
                    role,
                ],
                env=env_for(directory),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()
    return result


def progress(directory, processes, after, seconds=15):
    deadline = time.monotonic() + seconds + 60
    while True:
        live(processes)
        writes = records(directory / "writer.jsonl", live=True)
        reads = records(directory / "reader.jsonl", live=True)
        if (
            time.time() - after >= seconds
            and sum(e["stage"] == "ack" and e["time"] >= after for e in writes) >= 10
            and sum(e["stage"] == "search" and e["time"] >= after for e in reads) >= 10
            and sum(e["stage"] == "get" and e["time"] >= after for e in reads) >= 10
        ):
            return
        if time.monotonic() > deadline:
            raise TimeoutError("independent traffic did not make required progress")
        time.sleep(0.5)


def stop_role(directory, processes, role):
    (directory / ("stop-" + role)).touch()
    try:
        code = processes[role].wait(timeout=45)
    except subprocess.TimeoutExpired:
        run(["docker", "stop", "-t", "10", PROJECT + "-" + role], timeout=30)
        raise RuntimeError("traffic failed to obey stop marker")
    if code:
        raise RuntimeError(
            role + " failed: " + (directory / (role + ".log")).read_text()[-2000:]
        )
    if not read(directory / (role + ".json"))["stopped_by_controller"]:
        raise RuntimeError("traffic expired before controller stop")


def replace_disk(directory, target, before):
    """Destroy only a positively fenced, exact project-owned native data volume."""
    old = before[target]
    volumes = [
        m
        for m in old["Mounts"]
        if m["Destination"] == "/opt/vespa/var" and m["Type"] == "volume"
    ]
    if len(volumes) != 1:
        raise RuntimeError("native storage is not one named volume")
    name = volumes[0]["Name"]
    volume = json.loads(run(["docker", "volume", "inspect", name]))[0]
    if volume["Labels"].get("com.docker.compose.project") != PROJECT:
        raise RuntimeError("native volume ownership is unknown")
    current = inspect()[target]
    if current["Id"] != old["Id"] or current["State"]["Running"]:
        raise RuntimeError("old native owner is not positively fenced")
    run(["docker", "rm", old["Id"]], timeout=30)
    # Successful daemon listing, not a failed inspection, proves exact absence.
    if run(
        ["docker", "ps", "-aq", "--no-trunc", "--filter", "id=" + old["Id"]], timeout=30
    ).strip():
        raise RuntimeError("old native container still exists")
    save(
        directory / "disk-transition.json",
        {"phase": "fenced", "old_container": old["Id"], "old_volume": volume},
    )
    run(["docker", "volume", "rm", name], timeout=30)
    run(
        [
            "docker",
            "volume",
            "create",
            "--label",
            "com.docker.compose.project=" + PROJECT,
            "--label",
            "com.docker.compose.volume=" + target,
            name,
        ],
        timeout=30,
    )
    new = json.loads(run(["docker", "volume", "inspect", name]))[0]
    if new["CreatedAt"] == volume["CreatedAt"]:
        raise RuntimeError("volume birth identity did not change")
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "128m",
            "--label",
            "com.docker.compose.project=" + PROJECT,
            "--label",
            "com.docker.compose.oneoff=True",
            "--mount",
            f"type=volume,source={name},target=/empty,readonly",
            "--entrypoint",
            "python",
            "vespa-product-tools:dev",
            "-c",
            "import os; assert os.listdir('/empty') == [], 'replacement is not empty'",
        ],
        timeout=30,
    )
    save(
        directory / "disk-transition.json",
        {
            "phase": "empty-replacement",
            "old_container": old["Id"],
            "old_volume": volume,
            "new_volume": new,
        },
    )
    compose("up", "-d", target)
    current = inspect()
    if current[target]["Id"] == old["Id"]:
        raise RuntimeError("native container identity was reused")
    fixture = read(BASE / "fixture.json")
    fixture["containers"][target] = identity({target: current[target]})[target]
    save(BASE / "fixture.json", fixture)


def rebuilt_ack_reply(data, required, attempts):
    # The pinned CLI prints absence as a whole unquoted line, outside JSON.
    replies = json.loads(
        "".join(
            "null\n" if line.strip() == "Document not found." else line
            for line in data.splitlines(keepends=True)
        )
    )
    if not isinstance(replies, list) or len(replies) != len(required):
        raise RuntimeError("wrong rebuilt-member reply count/type")
    selected = None
    for reply, row in zip(replies, required):
        if reply is None:
            continue
        if (
            not isinstance(reply, dict)
            or reply.get("id") != "id:catalog:product::" + row["id"]
        ):
            raise RuntimeError("wrong rebuilt-member reply identity")
        fields = reply.get("fields", {})
        if set(fields) != {"pid", "tenant", "stock", "price_cents", "version"} or any(
            type(v) is not int for v in fields.values()
        ):
            raise RuntimeError("incomplete or invalid rebuilt-member reply")
        actual = {
            "id": str(fields["pid"]),
            **{k: fields[k] for k in ["tenant", "stock", "price_cents", "version"]},
        }
        if (
            actual["id"] != row["id"]
            or actual["version"] < row["version"]
            or attempts.get((actual["id"], actual["version"])) != actual
        ):
            raise RuntimeError("rebuilt member cannot serve the post-fence ACK")
        selected = selected or (row, actual)
    if selected is None:
        raise RuntimeError("no fresh ACK candidate stored on rebuilt physical member")
    return selected


def prove_rebuilt_read(directory, service, acknowledged):
    # A sharded member owns a subset: discover a real fresh owned key instead of
    # assuming the last ACK belongs to this physical node.
    required = {}
    for event in reversed(acknowledged):
        for row in reversed(event["rows"]):
            required.setdefault(row["id"], row)
        if len(required) >= (256 if MODE == "sharded" else 1):
            break
    required = list(required.values())
    number = CONTENT.index(service)
    args = [
        "timeout",
        "--signal=KILL",
        "45",
        "vespa-get",
        "--cluster",
        "catalog",
        "--replica-override",
        str(number),
        "--fieldset",
        "product:pid,tenant,stock,price_cents,version",
        "--timeout",
        "5",
    ]
    command = (
        "printf '%s\\n' "
        + shlex.join(["id:catalog:product::" + row["id"] for row in required])
        + " | "
        + shlex.join(args)
    )
    data = compose(
        "exec",
        "-T",
        "-e",
        "LANG=C.UTF-8",
        "-e",
        "JAVA_TOOL_OPTIONS=-Xmx128m -Dfile.encoding=UTF-8",
        service,
        "/bin/bash",
        "-o",
        "pipefail",
        "-c",
        command,
        timeout=60,
    )
    (directory / "rebuilt-native-reply.txt").write_text(data)
    save(directory / "rebuilt-read-candidates.json", required)
    attempts = {
        (r["id"], r["version"]): r
        for event in records(directory / "writer.jsonl", live=True)
        if event["stage"] == "attempt"
        for r in event["rows"]
    }
    row, actual = rebuilt_ack_reply(data, required, attempts)
    save(
        directory / "rebuilt-read-proof.json",
        {
            "service": service,
            "replica": number,
            "required_ack": row,
            "actual": actual,
            "candidate_count": len(required),
            "time": time.time(),
            "scope": "physical directed get of a newly attempted/ACKed mutation while a different member remains stopped",
        },
    )


def active_controller(state):
    host = urlparse(state["native"]["endpoint"]).hostname
    for service in SERVICES:
        if host in {service, f"vespa-products-{service}.vespa-product-search-net"}:
            return service
    raise RuntimeError("active controller host is not an owned service: " + str(host))


def require_reconciled_witness(prior):
    cases = sorted(
        (BASE / "cases").glob("*/case.json"),
        key=lambda p: (
            read(p)["start_version"],
            read(p).get("created_at", p.stat().st_mtime),
        ),
    )
    if not cases:
        raise RuntimeError("qualify a full native proof before destructive faults")
    path = cases[-1].parent / "final-proof.json"
    if not path.exists():
        raise RuntimeError(
            "latest history lacks final native proof; run test to recover first"
        )
    proof = read(path)
    if (
        proof["floor_sha256"] != digest(prior)
        or proof.get("all_native_versions_agree") is not True
    ):
        raise RuntimeError(
            "latest final proof does not cover the complete converged witness"
        )
    if any(proof[key] for key in ["missing", "stale", "wrong_payloads"]):
        raise RuntimeError("latest native proof failed")


def failed_report(directory, error):
    value = {"status": "failed", "error": str(error)}
    try:
        value.update(summarize(directory))
    except (ValueError, KeyError, TypeError, OSError) as reporting_error:
        value["reporting_error"] = str(reporting_error)
    save(directory / "report.json", value)


def stop_stack(crash):
    if crash:
        compose("kill", "-s", "SIGKILL", *SERVICES)
    else:
        compose("stop", "-t", "20", *SERVICES)
    stopped = inspect()
    if set(stopped) != set(SERVICES) or any(
        v["State"]["Running"] for v in stopped.values()
    ):
        raise RuntimeError("whole-stack stop not proved")
    if crash and any(
        v["State"]["ExitCode"] != 137 or v["State"]["OOMKilled"]
        for v in stopped.values()
    ):
        raise RuntimeError("whole-stack SIGKILL not proved")
    return stopped


def case(action, target=None, rebuilt=None):
    fixture = binding()
    prior = history()
    whole_stack = action in {"cold-reopen", "crash-reopen"}
    if action in {"crash", "disk", "rebuilt-crash"} or whole_stack:
        require_reconciled_witness(prior)
    start = (
        max(
            (v for values in prior["attempted_versions"].values() for v in values),
            default=0,
        )
        + 1
    )
    directory = BASE / "cases" / (f"{start:09d}-" + action + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    save(directory / "history.json", prior)
    save(
        directory / "case.json",
        {
            "fixture_id": fixture["id"],
            "mode": MODE,
            "start_version": start,
            "created_at": time.time(),
            "controller_sha256": hashlib.sha256(CONTROLLER_SOURCE).hexdigest(),
            "action": action,
            "target": target,
        },
    )
    before = inspect()
    save(directory / "before.json", before)
    save(
        directory / "source.json",
        {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in ROOT.rglob("*")
            if path.is_file()
            and not any(
                part in {"results", ".build", "__pycache__"}
                for part in path.relative_to(ROOT).parts
            )
        },
    )
    (directory / "controller-source.py").write_bytes(CONTROLLER_SOURCE)
    processes = {}
    restored = False
    faulted = False
    failed = None
    try:
        initial = wait_native(directory)
        controller = active_controller(initial)
        if target == "controller":
            target = controller
        elif target == "replica":
            target = (
                CONTENT[-1]
                if MODE == "sharded"
                else next(service for service in CONTENT if service != controller)
            )
        if target is not None and target not in SERVICES:
            raise ValueError("fault target is not owned")
        save(
            directory / "target.json",
            {"service": target, "active_controller": controller},
        )
        native_before = process_witness()
        save(directory / "native-processes-before.json", native_before)
        marker(directory, "baseline")
        processes = traffic(directory)
        progress(directory, processes, time.time())
        if target:
            marker(directory, "fault")
            faulted = True
            compose("kill", "-s", "SIGKILL", target)
            down = inspect()
            if (
                down[target]["State"]["Running"]
                or down[target]["State"]["OOMKilled"]
                or down[target]["State"]["ExitCode"] != 137
            ):
                raise RuntimeError("SIGKILL not proved")
            save(directory / "fault-inspect.json", down)
            # Observe read availability even while controller election blocks writes.
            fault_time = time.time()
            until = time.monotonic() + int(
                os.environ.get(
                    "NODE_DOWN_SECONDS", "180" if MODE == "cluster" else "60"
                )
            )
            while time.monotonic() < until:
                live(processes)
                time.sleep(0.5)
            job(directory, ["/app/replica_audit.py", "cluster"], processes)
            degraded = read(directory / "cluster-state.json")
            if target == controller:
                elected = active_controller({"native": degraded})
                if elected == target:
                    raise RuntimeError("controller role did not leave the fenced host")
                save(
                    directory / "controller-failover.json",
                    {
                        "old": controller,
                        "observed_active": elected,
                        "time": time.time(),
                        "seconds_since_fence": time.time() - fault_time,
                        "scope": "observed after deliberate hold, not election duration",
                    },
                )
            if target in CONTENT:
                index = CONTENT.index(target)
                node = degraded["state"]["service"]["storage"]["node"][str(index)]
                if node["state"]["generated"]["state"] == "up":
                    raise RuntimeError("native down state not observed")
            writes = records(directory / "writer.jsonl", live=True)
            reads = records(directory / "reader.jsonl", live=True)
            attempted = {
                (row["id"], row["version"]): e["time"]
                for e in writes
                if e["stage"] == "attempt"
                for row in e["rows"]
            }
            fresh_acks = [
                e
                for e in writes
                if e["stage"] == "ack"
                and all(
                    attempted[(row["id"], row["version"])] >= fault_time
                    for row in e["rows"]
                )
            ]
            if not fresh_acks:
                raise RuntimeError(
                    "no post-fence attempted writes acknowledged while member was absent"
                )
            save(
                directory / "degraded-progress.json",
                {
                    "fault_time": fault_time,
                    "new_attempt_ack_rows": sum(len(e["rows"]) for e in fresh_acks),
                    "first_new_attempt_ack_time": min(e["time"] for e in fresh_acks),
                },
            )
            if not any(
                e["stage"] == "search" and e["time"] >= fault_time for e in reads
            ):
                raise RuntimeError("no successful searches while one member was absent")
            if rebuilt is not None:
                for phase in ["before", "after"]:
                    owner = inspect()[target]
                    if owner["Id"] != before[target]["Id"] or owner["State"]["Running"]:
                        raise RuntimeError(
                            "failed peer is not still fenced during rebuilt read"
                        )
                    save(directory / ("rebuilt-peer-" + phase + ".json"), owner)
                    if phase == "before":
                        prove_rebuilt_read(directory, rebuilt, fresh_acks)
            marker(directory, "restart")
            if action == "disk":
                replace_disk(directory, target, before)
            else:
                compose("start", target)
            restored = True
        if whole_stack:
            marker(directory, "fault")
            faulted = True
            stopped = stop_stack(action == "crash-reopen")
            save(
                directory
                / (
                    "crash-stopped.json"
                    if action == "crash-reopen"
                    else "cold-stopped.json"
                ),
                stopped,
            )
            until = time.monotonic() + 15
            while time.monotonic() < until:
                live(processes)
                time.sleep(0.5)
            marker(directory, "restart")
            compose("start", *SERVICES)
            restored = True
        marker(directory, "native-repair")
        wait_native(directory, processes)
        floor = History(prior)
        for event in records(directory / "writer.jsonl", live=True):
            floor.append(event)
        marker(directory, "live-copy-proof")
        proof(directory, floor.value(), processes, prefix="live")
        marker(directory, "after-repair")
        progress(directory, processes, time.time())
        stop_role(directory, processes, "writer")
        processes.pop("writer")
        marker(directory, "reconcile")
        current = History(prior)
        for event in records(directory / "writer.jsonl"):
            current.append(event)
        proof(directory, current.value(), processes, prefix="final")
        job(directory, ["/app/search_checks.py"], processes)
        stop_role(directory, processes, "reader")
        processes.pop("reader")
        after = inspect()
        save(directory / "after.json", after)
        native_after = process_witness()
        save(directory / "native-processes-after.json", native_after)
        proof_started = next(
            mark["time"]
            for mark in records(directory / "phases.jsonl")
            if mark["phase"] == "live-copy-proof"
        )
        if any(
            role["started_unix"] + 1 > proof_started
            for roles in native_after.values()
            for role in roles.values()
        ):
            raise RuntimeError("native daemon restarted during copy verification")
        for service in SERVICES:
            if (
                not whole_stack
                and service != target
                and native_before[service] != native_after[service]
            ):
                raise RuntimeError(
                    "unrelated supervised native daemon changed: " + service
                )
        for name in SERVICES:
            if whole_stack:
                old = identity({name: before[name]})[name]
                new = identity({name: after[name]})[name]
                old.pop("started")
                new.pop("started")
                if old != new:
                    raise RuntimeError(
                        "retained reopen replaced a native container/image/volume"
                    )
            if (
                not whole_stack
                and name != target
                and identity({name: before[name]}) != identity({name: after[name]})
            ):
                raise RuntimeError("unrelated native member changed: " + name)
        marker(directory, "verified")
        result = {
            "status": "passed",
            "case": str(directory.relative_to(ROOT)),
            **summarize(directory),
        }
        save(directory / "report.json", result)
        print(json.dumps(result), flush=True)
    except Exception as error:
        failed = str(error)
        marker(directory, "aborted")
        failed_report(directory, error)
        raise
    finally:
        if whole_stack and faulted and not restored:
            compose("start", *SERVICES)
        if faulted and target in SERVICES and not restored:
            if target in inspect():
                compose("start", target)
            else:
                print(
                    "Native owner removed; resume the recorded disk transition before further faults",
                    flush=True,
                )
        for role, process in processes.items():
            (directory / ("stop-" + role)).touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                run(["docker", "stop", "-t", "10", PROJECT + "-" + role], timeout=30)
        if failed is not None:
            shutil.copyfile(
                directory / "report.json", directory / "failure-at-detection.json"
            )
            failed_report(directory, failed)


def owned_jobs():
    return run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
            "--filter",
            "label=com.docker.compose.oneoff=True",
        ]
    ).split()


def down():
    if owned_jobs():
        raise RuntimeError(
            "one-shot jobs still exist; preserve/stop them before teardown"
        )
    fixture = read(BASE / "fixture.json")
    cases = sorted(
        (BASE / "cases").glob("*/case.json"),
        key=lambda p: (
            read(p)["start_version"],
            read(p).get("created_at", p.stat().st_mtime),
        ),
    )
    latest = cases[-1].parent / "report.json" if cases else None
    if latest is None or not latest.exists() or read(latest)["status"] != "passed":
        raise RuntimeError(
            "latest fixture is not qualified; recover it before teardown"
        )
    if fixture["status"] == "ready":
        binding()
        require_reconciled_witness(history())
    compose(
        "exec",
        "-T",
        "node0",
        "curl",
        "-fsS",
        "--max-time",
        "20",
        "-X",
        "DELETE",
        "http://localhost:19071/application/v2/tenant/default/application/default/environment/prod/region/default/instance/default",
    )
    apps = json.loads(
        compose(
            "exec",
            "-T",
            "node0",
            "curl",
            "-fsS",
            "--max-time",
            "10",
            "http://localhost:19071/application/v2/tenant/default/application/",
        )
    )
    if apps:
        raise RuntimeError("native application deletion did not converge")
    fixture["status"] = "removed"
    save(BASE / "fixture.json", fixture)
    save(
        BASE / "application-removed.json",
        {"time": time.time(), "native_applications": apps},
    )


def destroy():
    if owned_jobs():
        raise RuntimeError("owned jobs remain; refusing teardown")
    fixture = read(BASE / "fixture.json") if (BASE / "fixture.json").exists() else None
    if fixture and fixture["status"] != "removed":
        raise RuntimeError("run down before destroying native storage")
    inspect()  # Refuse unexpected services or another mode before destruction.
    compose(
        "--profile",
        "cluster",
        "--profile",
        "sharded",
        "--profile",
        "tools",
        "down",
        "--volumes",
        "--remove-orphans",
    )
    if run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).strip():
        raise RuntimeError("owned containers remain")
    if run(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).strip():
        raise RuntimeError("owned volumes remain")
    if run(
        [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).strip():
        raise RuntimeError("owned networks remain")
    tags = set(
        run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"]).split()
    )
    for image in [
        "vespa-product-tools:dev",
        "vespa-inventory-workload:dev",
        "vespa-products-corpus:dev",
    ]:
        if image in tags:
            run(["docker", "image", "rm", image])
    (ROOT / "results/infrastructure.json").unlink(missing_ok=True)
    if fixture:
        destination = BASE / "previous-runs" / fixture["id"]
        destination.mkdir(parents=True, exist_ok=False)
        hashes = {}
        for path in BASE.rglob("*"):
            if path.is_file() and not path.is_relative_to(BASE / "previous-runs"):
                with path.open("rb") as source:
                    hashes[str(path.relative_to(BASE))] = hashlib.file_digest(
                        source, "sha256"
                    ).hexdigest()
        for child in BASE.iterdir():
            if child.name != "previous-runs":
                shutil.move(str(child), destination / child.name)
        for name, expected in hashes.items():
            with (destination / name).open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                    raise RuntimeError("archived witness changed")
        save(
            destination / "archive-integrity.json",
            {"sha256": hashes, "verified_files": len(hashes), "time": time.time()},
        )


def main():
    action = sys.argv[1]
    if action == "docker-up":
        from corpus import main as corpus
        from prepare_build import prepare

        infrastructure = ROOT / "results/infrastructure.json"
        if infrastructure.exists() and read(infrastructure)["mode"] != MODE:
            raise RuntimeError(
                "another mode owns the infrastructure; finish its lifecycle first"
            )
        existing = inspect()
        if existing and not infrastructure.exists():
            raise RuntimeError("unbound native resources already exist")
        corpus()
        prepare()
        compose("build", "tools", "inventory-workload")
        compose("up", "-d", "--wait", *SERVICES, timeout=900)
        save(
            ROOT / "results/infrastructure.json",
            {"mode": MODE, "containers": identity(inspect())},
        )
    elif action == "up":
        if (BASE / "fixture.json").exists():
            binding()
            case("retained-setup")
            return
        state = inspect()
        if set(state) != set(SERVICES):
            raise RuntimeError("run docker-up first")
        BASE.mkdir(parents=True, exist_ok=True)
        save(
            BASE / "fixture.json",
            {
                "id": uuid.uuid4().hex,
                "mode": MODE,
                "containers": identity(state),
                "status": "loading",
            },
        )
        save(BASE / "history.json", empty())
        # Refuse to load over any previously deployed application.
        existing = compose(
            "exec",
            "-T",
            "node0",
            "curl",
            "-fsS",
            "http://localhost:19071/application/v2/tenant/default/application/",
        )
        if json.loads(existing):
            raise RuntimeError("unbound native application already exists")
        load_before = resource_counters()
        save(BASE / "load-resource-before.json", load_before)
        job(BASE, ["/app/client.py", "load"])
        load_after = resource_counters()
        save(BASE / "load-resource-after.json", load_after)
        if identity(state) != identity(inspect()):
            raise RuntimeError("native container changed during initial load")
        save(
            BASE / "load-resource-counters.json",
            {
                "per_service": {
                    service: cgroup_cpu_seconds(
                        load_before[service]["counters"],
                        load_after[service]["counters"],
                    )
                    | {
                        "elapsed_seconds": load_after[service]["monotonic"]
                        - load_before[service]["monotonic"],
                    }
                    for service in SERVICES
                },
                "scope": "Native SUT cgroups around initial deploy/feed/index/logical verification; excludes image builds and pre-load server bootstrap. Raw before/after files include cgroup-lifetime memory peaks and anon/file/kernel counters; these are not process RSS or synchronized whole-stack peaks.",
            },
        )
        fixture = read(BASE / "fixture.json")
        fixture["status"] = "ready"
        fixture["corpus_sha256"] = read(ROOT / "results/corpus/prepared/manifest.json")[
            "sha256"
        ]
        fixture["images"] = {
            service: value["Image"] for service, value in state.items()
        }
        save(BASE / "fixture.json", fixture)
        job(BASE, ["/app/client.py", "copy-contract-test"])
        case("baseline")
    elif action == "test":
        binding()
        case("correctness")
    elif action == "crash":
        if MODE == "single":
            raise RuntimeError("no HA claim for one node")
        target = os.environ.get(
            "FAULT_SERVICE", "controller" if MODE == "cluster" else CONTENT[0]
        )
        if target not in SERVICES + ["controller", "replica"]:
            raise ValueError("target must be owned or a native role")
        case("crash", target)
    elif action == "disk":
        if MODE == "single":
            raise RuntimeError("empty disk loss has no redundant source in single mode")
        target = os.environ.get(
            "FAULT_SERVICE",
            "replica" if MODE == "cluster" else CONTENT[len(CONTENT) // 3],
        )
        case("disk", target)
    elif action == "rebuilt-crash":
        transitions = sorted((BASE / "cases").glob("*/disk-transition.json"))
        if not transitions:
            raise RuntimeError("qualify a disk replacement first")
        previous = transitions[-1].parent
        if read(previous / "report.json")["status"] != "passed":
            raise RuntimeError("disk replacement is not qualified")
        rebuilt = read(previous / "target.json")["service"]
        target = next(service for service in CONTENT if service != rebuilt)
        case("rebuilt-crash", target, rebuilt)
    elif action in {"cold-reopen", "crash-reopen"}:
        case(action)
    elif action == "benchmark":
        binding()
        cases = sorted(
            (BASE / "cases").glob("*/case.json"),
            key=lambda p: (
                read(p)["start_version"],
                read(p).get("created_at", p.stat().st_mtime),
            ),
        )
        latest = cases[-1].parent / "report.json" if cases else None
        if latest is None or not latest.exists() or read(latest)["status"] != "passed":
            raise RuntimeError("qualify the fixture before benchmarking")
        require_reconciled_witness(history())
        directory = BASE / "benchmarks" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        (directory / "benchmark-controller.py").write_bytes(
            (ROOT / "benchmark.py").read_bytes()
        )
        (directory / "control-source.py").write_bytes(CONTROLLER_SOURCE)
        (directory / "metrics-source.py").write_bytes(
            (ROOT / "metrics.py").read_bytes()
        )
        log = directory / "benchmark.log"
        native_before = process_witness()
        owners_before = identity(inspect())
        counters_before = resource_counters()
        save(directory / "resource-counters-before.json", counters_before)
        with log.open("x") as stream:
            subprocess.run(
                [sys.executable, "benchmark.py"],
                env=env_for(directory),
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=1200,
            )
        counters_after = resource_counters()
        save(directory / "resource-counters-after.json", counters_after)
        if owners_before != identity(inspect()) or native_before != process_witness():
            raise RuntimeError("native container or daemon changed during benchmark")
        save(
            directory / "resource-counters.json",
            {
                "per_service": {
                    service: cgroup_cpu_seconds(
                        counters_before[service]["counters"],
                        counters_after[service]["counters"],
                    )
                    | {
                        "elapsed_seconds": counters_after[service]["monotonic"]
                        - counters_before[service]["monotonic"],
                        "before": counters_before[service]["counters"],
                        "after": counters_after[service]["counters"],
                    }
                    for service in SERVICES
                },
                "scope": "Native SUT cgroups, including housekeeping and read-only counter inspection; entire benchmark command including warmups/quality/setup, not individual timed-query windows. Memory includes charged file cache/kernel; lifetime peak is since cgroup creation/start and may include setup/recovery, not benchmark-only peak or process RSS. Sum of member peaks is not a simultaneous stack peak. Workload container is excluded; its 2-CPU/3-GiB budget is declared in Compose. benchmark.py samples native SUT containers only.",
            },
        )
        results = read(directory / "benchmark.json")["results"]
        failures = {name: result["failures"] for name, result in results.items()}
        passed = bool(failures) and all(
            type(count) is int and count == 0 for count in failures.values()
        )
        save(
            directory / "validation.json",
            {"status": "passed" if passed else "failed", "request_failures": failures},
        )
        if not passed:
            raise RuntimeError(
                "benchmark contains failed requests; raw evidence retained"
            )
        print("Benchmark evidence:", directory, flush=True)
    elif action == "state":
        native(BASE)
    elif action == "down":
        down()
    elif action == "docker-down":
        destroy()
    else:
        raise ValueError(action)


if __name__ == "__main__":
    os.chdir(ROOT)
    (ROOT / "results").mkdir(exist_ok=True)
    with (ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
