"""Owned Typesense lifecycles and preserved-history operational experiments."""

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from version_history import EventTail, History, empty, load_current, records
from metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_SOURCE = Path(__file__).read_bytes()
MODE = os.environ.get("MODE", "cluster")
if MODE not in {"single", "cluster", "sharded"}:
    raise ValueError("MODE must be single, cluster or sharded")
BASE = (ROOT / os.environ.get("RESULTS_DIR", "results/production/" + MODE)).resolve()
if not BASE.is_relative_to(ROOT / "results"):
    raise ValueError("RESULTS_DIR must be inside this example's results directory")
PROJECT = "typesense-product-search"
COMPOSE = ["docker", "compose", "--project-name", PROJECT] + (
    [] if MODE == "single" else ["--profile", MODE]
)
SERVICES = {
    f"ts{i}"
    for i in range(1, 2 if MODE == "single" else 10 if MODE == "sharded" else 4)
}
USER = f"{os.getuid()}:{os.getgid()}"
INFRA = ROOT / "results/infrastructure.json"


@contextmanager
def controller_lock():
    path = ROOT / "results/controller.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "another Typesense lifecycle/fault controller is active"
            ) from error
        yield


def run(args, *, env=None, timeout=1200):
    result = subprocess.run(
        args, env=env, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        print(result.stdout[-6000:], end="", flush=True)
        print(result.stderr[-6000:], end="", file=sys.stderr, flush=True)
        result.check_returncode()
    return result.stdout


def compose(*args, **kwargs):
    return run(COMPOSE + list(args), **kwargs)


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".tmp").open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path.with_suffix(".tmp"), path)


def env_for(directory):
    return os.environ | {
        "MODE": MODE,
        "RESULTS_DIR": os.path.relpath(directory, ROOT),
        "URLS": ",".join(
            f"http://ts{i}:8108" for i in range(1, 2 if MODE == "single" else 4)
        ),
    }


def live(processes):
    for role, process in processes.items():
        if process.poll() is not None:
            raise RuntimeError(role + " stopped before recovery completed")


def job(directory, args, processes=None, timeout=1100):
    name = PROJECT + "-check-" + uuid.uuid4().hex[:12]
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
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if processes:
                    live(processes)
                if time.monotonic() > deadline:
                    raise RuntimeError("native verification deadline exceeded")
                time.sleep(0.25)
            if process.returncode:
                print(log.read_text()[-6000:], flush=True)
                raise RuntimeError("native/tool job failed: " + " ".join(args))
        finally:
            if process.poll() is None:
                run(["docker", "stop", "--timeout", "5", name], timeout=30)
                process.wait(timeout=30)


def inventory():
    ids = run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
            "--filter",
            "label=com.docker.compose.oneoff=False",
        ]
    ).split()
    values = json.loads(run(["docker", "inspect", *ids])) if ids else []
    found = {}
    for value in values:
        labels = value["Config"]["Labels"]
        service = labels["com.docker.compose.service"]
        if (
            labels["com.docker.compose.project"] != PROJECT
            or service in found
            or service not in SERVICES
        ):
            raise RuntimeError("foreign, duplicate or different-mode infrastructure")
        found[service] = value
    if set(found) != SERVICES:
        raise RuntimeError("incomplete infrastructure")
    return found


def volumes(values):
    names = {
        m["Name"]
        for value in values.values()
        for m in value["Mounts"]
        if m["Type"] == "volume"
    }
    if len(names) != len(SERVICES):
        raise RuntimeError("expected one data volume per server")
    values = json.loads(run(["docker", "volume", "inspect", *sorted(names)]))
    if any(
        (v.get("Labels") or {}).get("com.docker.compose.project") != PROJECT
        for v in values
    ):
        raise RuntimeError("foreign data volume")
    return {v["Name"]: v["CreatedAt"] for v in values}


def source_hashes():
    paths = list(ROOT.glob("*.py")) + [ROOT / "Makefile", ROOT / "docker-compose.yml"]
    for name in ["app", "scripts", "server", "inventory-workload"]:
        paths.extend(
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file()
            and (
                path.suffix in {".go", ".py", ".mod", ".txt"}
                or path.name == "Dockerfile"
            )
        )
    corpus = ROOT / "retrieval-corpus"
    paths.extend([corpus / "Dockerfile", corpus / "manifest.json"])
    for name in ["app", "client"]:
        paths.extend(
            path
            for path in (corpus / name).iterdir()
            if path.suffix == ".py" or path.name == "requirements.txt"
        )
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(paths))
    }


def process_identity(value):
    return {
        "id": value["Id"],
        "restart_count": value["RestartCount"],
        "started": value["State"]["StartedAt"],
        "pid": value["State"]["Pid"],
        "image": value["Image"],
    }


def check_survivors(before, after, target=None):
    for service in SERVICES - ({target} if target else set()):
        if (
            process_identity(before[service]) != process_identity(after[service])
            or not after[service]["State"]["Running"]
        ):
            raise RuntimeError("unrelated native process restarted/stopped: " + service)


def fixture_check():
    fixture = read(BASE / "fixture.json")
    infra = read(INFRA)
    current = inventory()
    if (
        fixture["stage"] != "ready"
        or fixture["project"] != PROJECT
        or fixture["mode"] != MODE
        or infra["mode"] != MODE
        or fixture["volumes"] != volumes(current)
    ):
        raise RuntimeError(
            "unready/foreign fixture or changed data disks; preserve and diagnose"
        )
    if (
        fixture["corpus_sha256"]
        != read(ROOT / "results/corpus/prepared/manifest.json")["sha256"]
    ):
        raise RuntimeError("corpus changed after fixture binding")
    if any(
        current[name]["Image"] != fixture["server_images"][name] for name in SERVICES
    ):
        raise RuntimeError("native server image changed outside a declared upgrade")
    return current


def history():
    state = History(empty())
    fixture = read(BASE / "fixture.json")
    hashes = {}
    cases = [case for case in BASE.glob("fault-*") if case.is_dir()]
    # Fault kinds sort alphabetically, not by execution order. Versions span runs.
    cases.sort(key=lambda case: read(case / "case.json")["start_version"])
    for case in cases:
        metadata = read(case / "case.json")
        if metadata["fixture_id"] != fixture["id"] or metadata["mode"] != MODE:
            raise RuntimeError(
                "fault history belongs to a different fixture or topology"
            )
        path = case / "writer.jsonl"
        for event in records(path):
            state.append(event)
        if path.exists():
            hashes[case.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(
        BASE / "history.json",
        state.value()
        | {
            "attempted_rows": state.operations,
            "acknowledged_rows": state.acknowledgements,
            "case_journal_sha256": hashes,
        },
    )
    return state


def verify(directory, processes=None, prefix=False):
    job(
        directory,
        ["/app/replica_audit.py", "prefix" if prefix else "verify"],
        processes,
    )
    return read(
        directory
        / ("live-prefix/replica-proof.json" if prefix else "replica-proof.json")
    )


def retire_removed_fixture():
    marker = BASE / "teardown.json"
    if not marker.exists():
        return
    proof = read(marker)
    fixture = read(BASE / "fixture.json") if (BASE / "fixture.json").exists() else None
    if (
        proof["project"] != PROJECT
        or proof["mode"] != MODE
        or set(proof["remaining"])
        != {"containers", "volumes", "networks", "build_tags"}
        or proof["fixture_id"] != (fixture["id"] if fixture else None)
        or proof["finished"] is not True
        or any(proof["remaining"].values())
    ):
        raise RuntimeError("invalid teardown witness")
    archive = BASE / "previous-runs" / uuid.uuid4().hex
    archive.mkdir(parents=True)
    owned = {
        "fixture.json",
        "history.json",
        "teardown.json",
        "load.json",
        "correctness.json",
        "replica-proof.json",
        "native-state.json",
        "cleanup.json",
        "benchmark.json",
        "tenant-benchmark.json",
        "resources.json",
        "quality-baseline.json",
        "report.json",
        "convergence-attempts.json",
        "bootstrap-recovery.json",
    }
    for path in list(BASE.iterdir()):
        if path.name == "previous-runs":
            continue
        if path.name in owned or path.name.startswith(("fault-", "reopen-", "job-")):
            path.rename(archive / path.name)


def docker_up():
    if INFRA.exists() and not read(INFRA).get("removed"):
        infra = read(INFRA)
        if infra["mode"] != MODE or volumes(inventory()) != infra["volumes"]:
            raise RuntimeError(
                "complete the current topology's cleanup before changing MODE or disks"
            )
    else:
        labels = "label=com.docker.compose.project=" + PROJECT
        if run(["docker", "volume", "ls", "-q", "--filter", labels]).strip():
            raise RuntimeError(
                "unbound owned data volumes; no automatic adoption/reset"
            )
    run(["python3", 'scripts/corpus.py'], timeout=3600)
    run(["python3", "scripts/config.py"])
    run(["python3", 'scripts/prepare_build.py'])
    compose("build", "ts1", "tools", "inventory-workload", timeout=1800)
    compose("up", "-d", *sorted(SERVICES))
    current = inventory()
    save(
        INFRA,
        {
            "project": PROJECT,
            "mode": MODE,
            "volumes": volumes(current),
            "removed": False,
        },
    )
    compose(
        "up", "-d", "--wait", "--wait-timeout", "300", *sorted(SERVICES), timeout=360
    )


def up():
    retire_removed_fixture()
    current = inventory()
    infra = read(INFRA)
    if infra["mode"] != MODE or volumes(current) != infra["volumes"]:
        raise RuntimeError("unbound infrastructure")
    if (BASE / "fixture.json").exists():
        fixture_check()
        history()
        verify(BASE)
        return
    fixture = {
        "id": uuid.uuid4().hex,
        "project": PROJECT,
        "mode": MODE,
        "stage": "initializing",
        "volumes": volumes(current),
        "server_images": {name: current[name]["Image"] for name in SERVICES},
        "corpus_sha256": read(ROOT / "results/corpus/prepared/manifest.json")["sha256"],
    }
    save(BASE / "fixture.json", fixture)
    save(BASE / "history.json", empty())
    job(BASE, ["/app/replica_audit.py", "empty"])
    job(BASE, ["/app/client.py", "load"], timeout=1800)
    history()
    verify(BASE)
    fixture["stage"] = "ready"
    save(BASE / "fixture.json", fixture)


def phase(case, label):
    with (case / "phases.jsonl").open("a") as stream:
        stream.write(json.dumps({"phase": label, "time": time.time()}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print("PHASE", label, flush=True)


def progress(case, processes, since=0, shard=None):
    deadline = time.monotonic() + 120
    writes, reads = EventTail(case / "writer.jsonl"), EventTail(case / "reader.jsonl")
    acknowledgements = searches = gets = 0
    try:
        while time.monotonic() < deadline:
            live(processes)
            for event in writes.read():
                if event["stage"] == "ack" and event["time"] >= since:
                    acknowledgements += sum(
                        shard is None or MODE != "sharded" or row["tenant"] % 3 == shard
                        for row in event["rows"]
                    )
            for event in reads.read():
                if event["time"] >= since:
                    searches += event["stage"] == "search"
                    gets += event["stage"] == "get"
            if min(acknowledgements, searches, gets) >= 5:
                return
            time.sleep(0.25)
    finally:
        writes.close()
        reads.close()
    raise RuntimeError(
        "no independent write/search/read progress within the bounded wait"
    )


def select_target(state, kind, shard, fixture):
    prefix = {f"http://ts{3 * shard + i}:8108" for i in range(1, 4)}
    if kind == "rebuilt":
        target = fixture.get("replacements", {}).get(str(shard))
        if target is None:
            raise RuntimeError(
                "rebuilt-member crash requires a completed replacement for this shard"
            )
        return target
    wanted = "LEADER" if kind == "leader" else "FOLLOWER"
    candidates = sorted(
        url
        for url, value in state.items()
        if url in prefix and value["state"] == wanted
    )
    if not candidates or (wanted == "LEADER" and len(candidates) != 1):
        raise RuntimeError("native role selection failed")
    return candidates[-1].split(":")[1].strip("/")


def owned_volume(value):
    mounts = [
        m["Name"]
        for m in value["Mounts"]
        if m["Type"] == "volume" and m["Destination"] == "/data"
    ]
    if len(mounts) != 1:
        raise RuntimeError("target does not have exactly one named data volume")
    volume = json.loads(run(["docker", "volume", "inspect", mounts[0]]))[0]
    labels = volume.get("Labels") or {}
    if labels.get("com.docker.compose.project") != PROJECT:
        raise RuntimeError("refusing a foreign data volume")
    return {"name": volume["Name"], "created": volume["CreatedAt"]}


def replacement_volume(plan, value):
    labels = value.get("Labels") or {}
    if (
        value["Name"] != plan["old_volume"]["name"]
        or value["CreatedAt"] == plan["old_volume"]["created"]
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.volume") != plan["target"]
        or labels.get("examples.replacement") != plan["replacement_id"]
    ):
        raise RuntimeError(
            "replacement volume identity/ownership differs from the saved plan"
        )
    return {"name": value["Name"], "created": value["CreatedAt"]}


def replace_disk(case, target, before):
    original = owned_volume(before[target])
    if (
        read(BASE / "fixture.json")["volumes"].get(original["name"])
        != original["created"]
    ):
        raise RuntimeError("disk identity differs from the bound fixture")
    plan = {
        "target": target,
        "old_volume": original,
        "old_container": before[target]["Id"],
        "replacement_id": uuid.uuid4().hex,
    }
    save(case / "replacement.json", plan)
    current = inventory()
    if (
        current[target]["Id"] != plan["old_container"]
        or current[target]["State"]["Running"]
    ):
        raise RuntimeError("old owner is not the exact fenced container")
    check_survivors(before, current, target)
    compose("rm", "-f", target)
    # A successful runtime listing must prove fencing; a failed inspect is not proof.
    if plan["old_container"] in run(["docker", "ps", "-aq", "--no-trunc"]).split():
        raise RuntimeError("old owner still exists")
    if owned_volume(before[target]) != original:
        raise RuntimeError("old disk changed before removal")
    run(["docker", "volume", "rm", original["name"]])
    run(
        [
            "docker",
            "volume",
            "create",
            "--label",
            "com.docker.compose.project=" + PROJECT,
            "--label",
            "com.docker.compose.volume=" + target,
            "--label",
            "examples.replacement=" + plan["replacement_id"],
            original["name"],
        ]
    )
    value = json.loads(run(["docker", "volume", "inspect", original["name"]]))[0]
    replacement = replacement_volume(plan, value)
    save(case / "replacement.json", plan | {"new_volume": replacement})
    compose(
        "up", "-d", "--no-deps", "--wait", "--wait-timeout", "600", target, timeout=660
    )
    if owned_volume(inventory()[target]) != replacement:
        raise RuntimeError("native server did not mount the replacement disk")


def recover_disk():
    """Read-only reconciliation/rebinding after interruption; never delete storage."""
    if active_checks():
        raise RuntimeError("stop owned workloads/checks before recovery")
    case = (ROOT / os.environ.get("RECOVERY_CASE", "")).resolve()
    if case.parent != BASE or not case.name.startswith("fault-disk-"):
        raise ValueError(
            "RECOVERY_CASE must name a disk-failure case in this RESULTS_DIR"
        )
    fixture = read(BASE / "fixture.json")
    saved = read(case / "fixture.json")
    metadata = read(case / "case.json")
    plan = read(case / "replacement.json")
    target = plan["target"]
    if (
        fixture["stage"] != "ready"
        or fixture["project"] != PROJECT
        or fixture["mode"] != MODE
        or read(INFRA)["project"] != PROJECT
        or read(INFRA)["mode"] != MODE
        or read(INFRA).get("removed") is not False
        or target not in SERVICES
        or metadata["kind"] != "disk"
        or metadata["target"] != target
        or metadata["mode"] != MODE
        or metadata["fixture_id"] != fixture["id"]
        or saved["id"] != fixture["id"]
        or saved["project"] != PROJECT
        or saved["mode"] != MODE
        or saved["volumes"].get(plan["old_volume"]["name"])
        != plan["old_volume"]["created"]
        or fixture["server_images"] != saved["server_images"]
        or fixture["corpus_sha256"]
        != read(ROOT / "results/corpus/prepared/manifest.json")["sha256"]
    ):
        raise RuntimeError("recovery plan is not bound to this fixture")
    value = json.loads(
        run(["docker", "volume", "inspect", plan["old_volume"]["name"]])
    )[0]
    replacement = replacement_volume(plan, value)
    expected = saved["volumes"] | {replacement["name"]: replacement["created"]}
    if fixture["volumes"] not in (saved["volumes"], expected):
        raise RuntimeError("fixture changed independently of this replacement")
    compose(
        "up", "-d", "--no-deps", "--wait", "--wait-timeout", "600", target, timeout=660
    )
    current = inventory()
    if volumes(current) != expected or any(
        current[name]["Image"] != fixture["server_images"][name] for name in SERVICES
    ):
        raise RuntimeError("unrelated disks or server images changed")
    history()
    verify(BASE)
    fixture["volumes"] = expected
    fixture.setdefault("replacements", {})[str(metadata["shard"])] = target
    save(BASE / "fixture.json", fixture)
    save(INFRA, read(INFRA) | {"volumes": expected})
    save(
        case / "manual-recovery.json",
        {"verified": True, "time": time.time(), "mutations_replayed": False},
    )
    # The interrupted experiment remains failed; recovery is separate evidence.


def restore_target(case, target, kind, before):
    if kind == "disk" and (case / "replacement.json").exists():
        plan = read(case / "replacement.json")
        # Inspection failure is unknown state. Never let Compose create an
        # unplanned empty disk while attempting to clean up a failed replacement.
        current = owned_volume(before[target])
        if current != plan["old_volume"]:
            value = json.loads(run(["docker", "volume", "inspect", current["name"]]))[0]
            replacement_volume(plan, value)
    compose("up", "-d", "--no-deps", target)


def fault(kind):
    if kind not in {"leader", "follower", "disk", "rebuilt"}:
        raise ValueError(kind)
    if MODE == "single":
        raise RuntimeError("replicated fault cases require cluster or sharded MODE")
    shard = int(os.environ.get("FAULT_SHARD", "0"))
    if not 0 <= shard < (3 if MODE == "sharded" else 1):
        raise ValueError("FAULT_SHARD is outside this topology")
    before = fixture_check()
    prior = history()
    verify(BASE)
    fixture = read(BASE / "fixture.json")
    native = read(BASE / "replica-proof.json")["native_after"]
    target = select_target(native, kind, shard, fixture)
    case = BASE / (
        f"fault-{kind}-s{shard}-{time.strftime('%Y%m%dT%H%M%S')}-"
        + uuid.uuid4().hex[:8]
    )
    case.mkdir()
    save(case / "status.json", {"passed": False, "reason": "case not completed"})
    shutil.copy2(BASE / "history.json", case / "history.json")
    shutil.copy2(BASE / "fixture.json", case / "fixture.json")
    save(
        case / "case.json",
        {
            "mode": MODE,
            "kind": kind,
            "shard": shard,
            "target": target,
            "start_version": prior.last_version + 1,
            "fixture_id": fixture["id"],
            "controller_sha256": hashlib.sha256(CONTROLLER_SOURCE).hexdigest(),
            "source_sha256": source_hashes(),
            "native_commands": {
                name: before[name]["Config"]["Cmd"] for name in sorted(SERVICES)
            },
            "workload_image": run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    "typesense-products-inventory:dev",
                ]
            ).strip(),
            "verifier_image": run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    "typesense-product-tools:dev",
                ]
            ).strip(),
            "update_batch": int(os.environ.get("UPDATE_BATCH", "10")),
        },
    )
    save(case / "before.json", before)
    (case / "controller.py").write_bytes(CONTROLLER_SOURCE)
    processes, names = {}, {}
    injected = passed = False
    failure = "case did not complete"
    errors = []
    with ExitStack() as stack:
        try:
            for role in ["writer", "reader"]:
                name = PROJECT + "-" + role + "-" + uuid.uuid4().hex[:12]
                names[role] = name
                output = stack.enter_context((case / (role + ".log")).open("x"))
                processes[role] = subprocess.Popen(
                    COMPOSE
                    + [
                        "run",
                        "--rm",
                        "-T",
                        "--name",
                        name,
                        "--user",
                        USER,
                        "inventory-workload",
                        role,
                    ],
                    env=env_for(case),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            phase(case, "baseline")
            progress(case, processes)
            time.sleep(5)
            phase(case, "injection")
            injected = True
            compose("kill", "-s", "SIGKILL", target)
            stopped = json.loads(run(["docker", "inspect", before[target]["Id"]]))[0]
            if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 137:
                raise RuntimeError("target did not exit with SIGKILL")
            save(case / "stopped.json", stopped)
            phase(case, "outage")
            started = time.time()
            time.sleep(30)
            progress(case, processes, started, shard=shard)
            phase(case, "repair")
            if kind == "disk":
                replace_disk(case, target, before)
            else:
                compose(
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "--wait-timeout",
                    "600",
                    target,
                    timeout=660,
                )
            prefix = case / "live-prefix"
            prefix.mkdir()
            save(prefix / "history.json", load_current(case, live=True).value())
            proof = verify(case, processes, prefix=True)
            after = inventory()
            if after[target]["Image"] != before[target]["Image"]:
                raise RuntimeError("target server image changed during recovery")
            check_survivors(before, after, target)
            if kind != "disk" and (
                before[target]["Id"] != after[target]["Id"]
                or before[target]["State"]["StartedAt"]
                == after[target]["State"]["StartedAt"]
            ):
                raise RuntimeError(
                    "expected retained target container with a new native process"
                )
            phase(case, "after")
            progress(case, processes, time.time(), shard=shard)
            (case / "stop-writer").touch()
            if processes["writer"].wait(timeout=30):
                raise RuntimeError("writer failed")
            phase(case, "reconcile")
            final = verify(case, {"reader": processes["reader"]})
            check_survivors(after, inventory())
            (case / "stop-reader").touch()
            if processes["reader"].wait(timeout=30):
                raise RuntimeError("reader failed")
            if any(
                not read(case / (role + ".json"))["stopped_by_controller"]
                for role in processes
            ):
                raise RuntimeError("a generator expired before reconciliation")
            if kind == "disk":
                current_volumes = volumes(inventory())
                changed = [
                    name
                    for name in fixture["volumes"]
                    if fixture["volumes"][name] != current_volumes.get(name)
                ]
                if changed != [read(case / "replacement.json")["new_volume"]["name"]]:
                    raise RuntimeError("an unrelated data volume changed")
                fixture["volumes"] = current_volumes
                fixture.setdefault("replacements", {})[str(shard)] = target
                save(BASE / "fixture.json", fixture)
                save(INFRA, read(INFRA) | {"volumes": current_volumes})
            if source_hashes() != read(case / "case.json")["source_sha256"]:
                raise RuntimeError(
                    "source/configuration changed during the operational case"
                )
            phase(case, "verified")
            save(
                case / "result.json",
                {
                    "kind": kind,
                    "target": target,
                    "shard": shard,
                    "writer": read(case / "writer.json"),
                    "reader": read(case / "reader.json"),
                    "prefix_rows_checked": proof["rows_checked"],
                    "final_rows_checked": final["rows_checked"],
                    "vector_coordinates_checked": final["vector_coordinates_checked"],
                    "traffic": summarize(case),
                },
            )
            passed = True
        except Exception as error:
            failure = type(error).__name__ + ": " + str(error)
            raise
        finally:
            for role, process in processes.items():
                if process.poll() is None:
                    (case / ("stop-" + role)).touch()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        run(
                            ["docker", "stop", "--timeout", "5", names[role]],
                            timeout=30,
                        )
                        process.wait(timeout=30)
            if injected and not passed:
                try:
                    restore_target(case, target, kind, before)
                except (subprocess.SubprocessError, OSError, RuntimeError) as error:
                    errors.append(str(error))
            history()
            save(
                case / "status.json",
                {
                    "passed": passed,
                    "reason": None if passed else failure,
                    "cleanup_errors": errors,
                },
            )
            if errors:
                raise RuntimeError("owned fault recovery failed: " + "; ".join(errors))


def active_checks():
    return run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
            "--filter",
            "label=com.docker.compose.oneoff=True",
        ]
    ).split()


def down():
    if active_checks():
        raise RuntimeError(
            "owned workloads/checks are still running; stop them before cleanup"
        )
    fixture_check()
    job(BASE, ["/app/replica_audit.py", "drop"])
    fixture = read(BASE / "fixture.json")
    fixture["stage"] = "dropped"
    save(BASE / "fixture.json", fixture)


def docker_down():
    if active_checks():
        raise RuntimeError(
            "owned workloads/checks are still running; stop them before teardown"
        )
    fixture = read(BASE / "fixture.json") if (BASE / "fixture.json").exists() else None
    if (
        INFRA.exists()
        and not read(INFRA).get("removed")
        and read(INFRA)["mode"] != MODE
    ):
        raise RuntimeError("MODE does not match the running infrastructure")
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
    tags = [
        "typesense-products-server:dev",
        "typesense-product-tools:dev",
        "typesense-products-inventory:dev",
        "typesense-products-corpus:dev",
    ]
    for tag in tags:
        if run(["docker", "image", "ls", "-q", tag]).strip():
            run(["docker", "image", "rm", tag])
    label = "label=com.docker.compose.project=" + PROJECT
    remaining = {
        "containers": run(["docker", "ps", "-aq", "--filter", label]).split(),
        "volumes": run(["docker", "volume", "ls", "-q", "--filter", label]).split(),
        "networks": run(["docker", "network", "ls", "-q", "--filter", label]).split(),
        "build_tags": [
            tag for tag in tags if run(["docker", "image", "ls", "-q", tag]).strip()
        ],
    }
    if any(remaining.values()):
        raise RuntimeError("owned resources remain: " + str(remaining))
    save(
        BASE / "teardown.json",
        {
            "project": PROJECT,
            "mode": MODE,
            "finished": True,
            "fixture_id": fixture["id"] if fixture else None,
            "remaining": remaining,
        },
    )
    save(INFRA, {"project": PROJECT, "mode": MODE, "removed": True})


def recover_bootstrap():
    fixture = read(BASE / "fixture.json")
    if (
        fixture["stage"] != "initializing"
        or fixture["mode"] != MODE
        or fixture["project"] != PROJECT
        or fixture["volumes"] != volumes(inventory())
        or any(BASE.glob("fault-*"))
    ):
        raise RuntimeError(
            "bootstrap recovery requires the same initial unexercised data volumes"
        )
    history()
    verify(BASE)
    fixture["stage"] = "ready"
    save(BASE / "fixture.json", fixture)
    save(
        BASE / "bootstrap-recovery.json",
        {"verified": True, "mutations_replayed": False},
    )


def cold_reopen():
    if active_checks():
        raise RuntimeError("workloads/checks are still active")
    before = fixture_check()
    history()
    case = BASE / ("reopen-" + uuid.uuid4().hex)
    case.mkdir()
    started = time.monotonic()
    save(case / "before.json", before)
    passed = False
    try:
        compose("stop", "--timeout", "30", *sorted(SERVICES), timeout=120)
        offline = inventory()
        if any(v["State"]["Running"] for v in offline.values()):
            raise RuntimeError("full native-process shutdown was incomplete")
        save(case / "offline.json", offline)
        compose(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "600",
            *sorted(SERVICES),
            timeout=660,
        )
        current = fixture_check()
        for name in SERVICES:
            if (
                before[name]["Id"] != current[name]["Id"]
                or before[name]["State"]["StartedAt"]
                == current[name]["State"]["StartedAt"]
            ):
                raise RuntimeError(
                    "expected retained storage/containers with new native processes"
                )
        verify(BASE)
        shutil.copy2(BASE / "replica-proof.json", case / "replica-proof.json")
        passed = True
    finally:
        if not passed:
            compose("up", "-d", *sorted(SERVICES))
        save(
            case / "result.json",
            {
                "passed": passed,
                "seconds": time.monotonic() - started,
                "scope": "whole-stack shutdown persistence with warm host caches; no availability claim",
            },
        )


def test():
    fixture_check()
    history()
    verify(BASE)
    job(BASE, ["/app/search_checks.py"])


def benchmark():
    if active_checks():
        raise RuntimeError("stop active workloads/checks before benchmarking")
    fixture_check()
    history()
    verify(BASE)
    output = run(
        ["python3", 'scripts/benchmark.py'],
        env=env_for(BASE) | {"COMPOSE_PROJECT_NAME": PROJECT},
        timeout=1800,
    )
    print(output, end="", flush=True)


def dispatch():
    os.chdir(ROOT)
    BASE.mkdir(parents=True, exist_ok=True)
    action = sys.argv[1]
    if action == "docker-up":
        docker_up()
    elif action == "up":
        up()
    elif action == "verify":
        fixture_check()
        history()
        verify(BASE)
    elif action in {"leader", "follower", "disk", "rebuilt"}:
        fault(action)
    elif action == "down":
        down()
    elif action == "docker-down":
        docker_down()
    elif action == "recover-disk":
        recover_disk()
    elif action == "recover-bootstrap":
        recover_bootstrap()
    elif action == "cold-reopen":
        cold_reopen()
    elif action == "test":
        test()
    elif action == "benchmark":
        benchmark()
    else:
        raise ValueError(action)


def main():
    with controller_lock():
        dispatch()


if __name__ == "__main__":
    main()
