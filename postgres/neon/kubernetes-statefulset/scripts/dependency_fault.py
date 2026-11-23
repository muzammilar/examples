"""Native pageserver/cache, S3 gateway and WAL-quorum recovery under live load."""

from datetime import datetime, timezone
import json
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import time

from production_observations import summarize, timestamp
from fencing import prove_stopped

root = Path("results")
os.environ["KUBECONFIG"] = str((root / "kubeconfig").resolve())
kube = ["kubectl", "--context", "kind-neon-storage", "-n", "neon-lab"]
mode = sys.argv[1]
if mode not in ("pageserver-outage", "pageserver-cache", "s3", "wal-quorum", "backup"):
    raise ValueError(mode)
case = mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
ids = json.loads((root / "ids.json").read_text())
markers = [("before", datetime.now(timezone.utc))]
proof = {}
scaled, gateway = [], None
processes, logs = [], []
restore_created = False


def now():
    return datetime.now(timezone.utc)


def run(command, capture=False, timeout=180):
    result = subprocess.run(command, text=True, capture_output=capture, timeout=timeout)
    if result.returncode:
        if capture:
            with (root / (case + "-commands.log")).open("a") as output:
                output.write(result.stdout + result.stderr)
        raise RuntimeError(f"command failed {command}: {result.returncode}")
    return result.stdout


def k(*args, capture=False):
    return run([*kube, *args], capture=capture)


def tool(action):
    return k("exec", "client", "--", "sh", "/app/entrypoint.sh", action, capture=True)


def api(path, body=None, method="GET"):
    code = (
        "import json,sys,urllib.error,workload\n"
        "try:\n"
        f" print(json.dumps(workload.api({path!r},{body!r},{method!r})))\n"
        "except urllib.error.HTTPError as error:\n"
        " print(error.read().decode('utf-8','replace'),file=sys.stderr)\n"
        " raise\n"
    )
    return json.loads(k("exec", "client", "--", "python", "-c", code, capture=True))


def events(prefix):
    path = root / (prefix + case + ".jsonl")
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(after):
    end = time.monotonic() + 180
    while time.monotonic() < end:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("live workload exited")
        writes = sum(
            row["stage"] == "ack" and timestamp(row["at"]) > after
            for row in events("fault-")
        )
        reads = sum(
            row["outcome"] == "ok" and timestamp(row["at"]) > after
            for row in events("reads-")
        )
        if writes >= 3 and reads >= 3:
            return
        time.sleep(1)
    raise RuntimeError("no independent writer/reader progress")


def lsn(value):
    high, low = value.split("/")
    return int(high, 16) << 32 | int(low, 16)


def wal_barrier():
    frozen = max(
        (row["flush_lsn"] for row in events("fault-") if row["stage"] == "ack"), key=lsn
    )
    end = time.monotonic() + 180
    last = None
    while time.monotonic() < end:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("workload exited while WAL repaired")
        try:
            status = json.loads(tool("wal-status"))
            if len(status) == 3 and all(
                lsn(row["flush_lsn"]) >= lsn(frozen)
                and lsn(row["commit_lsn"]) >= lsn(frozen)
                for row in status.values()
            ):
                return {"frozen_ack_lsn": frozen, "status": status}
        except RuntimeError as error:
            last = str(error)
        time.sleep(2)
    raise RuntimeError("native WAL barrier did not converge: " + str(last))


def page_checkpoint(frozen):
    # /checkpoint is testing-only in this pinned production build. Temporarily
    # shorten ordinary checkpoint settings and observe native upload progress.
    config_path = f"tenant/{ids['tenant']}/config"
    previous = api(config_path)["tenant_specific_overrides"]
    (root / (case + "-checkpoint-original-overrides.json")).write_text(
        json.dumps(previous, indent=2) + "\n"
    )
    targets = {}
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        parent = api(f"tenant/{ids['tenant']}/timeline/{ids['timeline']}")
        if lsn(parent["last_record_lsn"]) >= lsn(frozen):
            break
        time.sleep(1)
    else:
        raise RuntimeError("pageserver did not ingest frozen ACK WAL")
    for name in ("timeline", "branch"):
        if name in ids:
            targets[name] = api(f"tenant/{ids['tenant']}/timeline/{ids[name]}")[
                "last_record_lsn"
            ]
    api(
        "tenant/config",
        {"tenant_id": ids["tenant"], "checkpoint_timeout": "5s"},
        "PATCH",
    )
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("live workload exited during checkpoint/upload")
            uploaded = {
                name: api(f"tenant/{ids['tenant']}/timeline/{ids[name]}")
                for name in targets
            }
            # A restarted idle timeline can report projected=0 while retaining
            # its already generation-validated visible upload LSN. Both fields
            # describe remote uploads; disk_consistent_lsn alone is insufficient.
            upload_lsns = {
                name: max(
                    (
                        state["remote_consistent_lsn"],
                        state["remote_consistent_lsn_visible"],
                    ),
                    key=lsn,
                )
                for name, state in uploaded.items()
            }
            (root / (case + "-checkpoint-pending.json")).write_text(
                json.dumps(
                    {
                        "targets": targets,
                        "native_timelines": uploaded,
                        "accepted_upload_lsns": upload_lsns,
                    },
                    indent=2,
                )
                + "\n"
            )
            if all(
                lsn(upload_lsns[name]) >= lsn(target)
                for name, target in targets.items()
            ):
                uploaded["checkpoint_control"] = {
                    "native_target_lsns": targets,
                    "temporary_checkpoint_timeout": "5s",
                    "checkpoint_distance_unchanged": True,
                    "previous_overrides": previous,
                }
                return uploaded
            time.sleep(2)
        raise RuntimeError("ordinary pageserver checkpoints did not upload frozen LSNs")
    finally:
        api(
            "tenant/config",
            {
                "tenant_id": ids["tenant"],
                "checkpoint_timeout": previous.get("checkpoint_timeout"),
                "checkpoint_distance": previous.get("checkpoint_distance"),
            },
            "PATCH",
        )


def backup_tool(action):
    source = Path('scripts/backup_workload.py').read_text()
    result = subprocess.run(
        [*kube, "exec", "-i", "client", "--", "python", "-", action, case],
        input=source,
        text=True,
        capture_output=True,
        timeout=300,
    )
    (root / (case + "-" + action + ".log")).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("logical backup/restore workload failed: " + action)


def restore_objects():
    image = json.loads(Path("images.json").read_text())["runner"]
    client = json.loads(k("get", "pod", "client", "-o", "json", capture=True))
    client_address = ipaddress.ip_address(client["status"]["podIP"])
    client_cidr = str(client_address) + (
        "/32" if client_address.version == 4 else "/128"
    )
    metadata = {
        "name": "restore",
        "namespace": "neon-lab",
        "labels": {"backup-case": case},
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": metadata,
        "spec": {
            "selector": {"app": "restore"},
            "ports": [{"port": 5432, "targetPort": 5432}],
        },
    }
    pod = {
        "nodeSelector": {"kubernetes.io/hostname": "neon-storage-control-plane"},
        "securityContext": {"runAsUser": 999, "runAsGroup": 999, "fsGroup": 999},
        "containers": [
            {
                "name": "restore",
                "image": image,
                "imagePullPolicy": "Never",
                "command": [
                    "/bin/sh",
                    "-ec",
                    "initdb -D /data/db -U restore --auth-local=trust --auth-host=trust; "
                    f"printf 'host postgres restore {client_cidr} trust\\n' >> /data/db/pg_hba.conf; "
                    "exec postgres -D /data/db -c listen_addresses='*' -c unix_socket_directories=/tmp -c fsync=on",
                ],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                },
                "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                "readinessProbe": {
                    "exec": {
                        "command": ["pg_isready", "-h", "127.0.0.1", "-U", "restore"]
                    },
                    "periodSeconds": 2,
                },
            }
        ],
        "volumes": [{"name": "data", "emptyDir": {}}],
    }
    stateful = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": metadata,
        "spec": {
            "serviceName": "restore",
            "replicas": 1,
            "selector": {"matchLabels": {"app": "restore"}},
            "template": {
                "metadata": {"labels": {"app": "restore", "backup-case": case}},
                "spec": pod,
            },
        },
    }
    return {"apiVersion": "v1", "kind": "List", "items": [service, stateful]}


def snapshot():
    pods = json.loads(k("get", "pods", "-o", "json", capture=True))["items"]
    return {
        pod["metadata"]["name"]: {
            "uid": pod["metadata"]["uid"],
            "containers": [
                {"id": container["containerID"], "restarts": container["restartCount"]}
                for container in pod["status"].get("containerStatuses", [])
            ],
        }
        for pod in pods
    }


def scale(name, replicas):
    k("scale", "statefulset/" + name, "--replicas=" + str(replicas))
    if replicas:
        k("rollout", "status", "statefulset/" + name, "--timeout=300s")
    else:
        k("wait", "--for=delete", "pod/" + name + "-0", "--timeout=120s")


for action in ("churn", "watch"):
    log = (root / (case + "-" + action + ".log")).open("w")
    logs.append(log)
    processes.append(
        subprocess.Popen(
            [
                *kube,
                "exec",
                "client",
                "--",
                "env",
                "FAULT_CASE=" + case,
                "/usr/local/bin/workload",
                action,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    )
try:
    progress(markers[0][1])
    proof["wal_before"] = wal_barrier()
    before = snapshot()
    excluded = (
        {"pageserver-0"}
        if mode.startswith("pageserver")
        else {"sk1-0", "sk2-0"}
        if mode == "wal-quorum"
        else set()
    )
    if mode == "backup":
        if "branch" not in ids:
            raise RuntimeError("run test-production branch setup before backup-restore")
        excluded = {"compute-0", "pageserver-0", "sk0-0", "sk1-0", "sk2-0", "branch-0"}
        existing = k(
            "get",
            "statefulset/restore",
            "service/restore",
            "--ignore-not-found",
            "-o",
            "name",
            capture=True,
        )
        if existing.strip():
            raise RuntimeError(
                "restore resources already exist; preserve the previous run"
            )
        begin_backup = time.monotonic()
        backup_tool("backup")
        proof["backup_seconds"] = time.monotonic() - begin_backup
        progress(now())
    if mode == "pageserver-cache":
        proof["uploaded_checkpoint_before"] = page_checkpoint(
            proof["wal_before"]["frozen_ack_lsn"]
        )
    started = time.monotonic()
    markers.append(("outage", now()))
    if mode == "backup":
        proof["fenced_source_processes"] = {}
        for name in ("compute", "branch", "pageserver", "sk0", "sk1", "sk2"):
            original = json.loads(
                k("get", "pod", name + "-0", "-o", "json", capture=True)
            )
            scaled.append(name)
            scale(name, 0)
            proof["fenced_source_processes"][name] = prove_stopped(original)
        restore_created = True
        subprocess.run(
            [*kube, "apply", "-f", "-"],
            input=json.dumps(restore_objects()),
            text=True,
            check=True,
            timeout=60,
        )
        k("rollout", "status", "statefulset/restore", "--timeout=180s")
        backup_tool("restore")
        proof["logical_restore"] = json.loads(
            (root / (case + "-restore.json")).read_text()
        )
        markers.append(("recovery", now()))
        for name in ("sk0", "sk1", "sk2", "pageserver", "compute", "branch"):
            scale(name, 1)
            scaled.remove(name)
    elif mode.startswith("pageserver"):
        original = json.loads(
            k("get", "pod", "pageserver-0", "-o", "json", capture=True)
        )
        claims = [
            volume["persistentVolumeClaim"]["claimName"]
            for volume in original["spec"]["volumes"]
            if "persistentVolumeClaim" in volume
        ]
        if (
            claims != ["data-pageserver-0"]
            or original["metadata"]["ownerReferences"][0]["name"] != "pageserver"
        ):
            raise RuntimeError("unexpected pageserver volume ownership")
        old_pvc = json.loads(k("get", "pvc", claims[0], "-o", "json", capture=True))
        scaled.append("pageserver")
        scale("pageserver", 0)
        proof["pageserver_fencing"] = prove_stopped(original)
        if mode == "pageserver-cache":
            k("delete", "pvc", claims[0], "--wait=true", "--timeout=120s")
        time.sleep(30)
        markers.append(("recovery", now()))
        scale("pageserver", 1)
        scaled.remove("pageserver")
        if mode == "pageserver-cache":
            replacement = json.loads(
                k("get", "pvc", claims[0], "-o", "json", capture=True)
            )
            if replacement["metadata"]["uid"] == old_pvc["metadata"]["uid"]:
                raise RuntimeError("pageserver cache volume not replaced")
            proof["cache_replacement"] = {
                "claim": claims[0],
                "old_uid": old_pvc["metadata"]["uid"],
                "new_uid": replacement["metadata"]["uid"],
            }
            generation = int(time.time())
            deadline = time.monotonic() + 180
            attach_errors = []
            tenant_conf = proof["uploaded_checkpoint_before"]["checkpoint_control"][
                "previous_overrides"
            ]
            while time.monotonic() < deadline:
                try:
                    # Idempotent reconciliation of the same fenced tenant and
                    # generation; wait for its Service/API, never replace its ID.
                    api(
                        f"tenant/{ids['tenant']}/location_config",
                        {
                            "mode": "AttachedSingle",
                            "generation": generation,
                            "tenant_conf": tenant_conf,
                        },
                        "PUT",
                    )
                    break
                except RuntimeError as error:
                    attach_errors.append(str(error))
                    time.sleep(2)
            else:
                raise RuntimeError("existing-tenant reattach did not converge")
            proof["reattach_transient_errors"] = attach_errors
            proof["reattach_generation"] = generation
    elif mode == "wal-quorum":
        for name in ("sk1", "sk2"):
            scaled.append(name)
            scale(name, 0)
        stopped = now()
        time.sleep(30)
        minority = [
            row
            for row in events("fault-")
            if row["stage"] == "ack"
            and timestamp(row["at"]).timestamp() > stopped.timestamp() + 10
        ]
        proof["minority_acked_rows_after_10s"] = sum(row["count"] for row in minority)
        if minority:
            raise RuntimeError("minority WAL unexpectedly acknowledged writes")
        markers.append(("recovery", now()))
        for name in list(scaled):
            scale(name, 1)
            scaled.remove(name)
    else:
        candidate = "neon-kind-support-s3-1"
        info = json.loads(run(["docker", "inspect", candidate], capture=True))[0]
        if (
            info["Config"]["Labels"].get("com.docker.compose.project")
            != "neon-kind-support"
            or info["Config"]["Labels"].get("com.docker.compose.service") != "s3"
        ):
            raise RuntimeError("refusing foreign S3 gateway")
        gateway = candidate
        proof["s3_address_before"] = info["NetworkSettings"]["Networks"][
            "neon-kind-support_default"
        ]["IPAddress"]
        run(["docker", "stop", "--timeout", "0", gateway])
        probe = subprocess.run(
            [
                *kube,
                "exec",
                "client",
                "--",
                "python",
                "-c",
                "import urllib.request,urllib.error\ntry:\n urllib.request.urlopen('http://s3:9000',timeout=5)\nexcept urllib.error.HTTPError:\n raise SystemExit(0)",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        proof["s3_unavailable_probe"] = {
            "returncode": probe.returncode,
            "stderr": probe.stderr,
        }
        if probe.returncode == 0:
            raise RuntimeError("S3 outage not proven")
        time.sleep(30)
        markers.append(("recovery", now()))
        run(["docker", "start", gateway])
        info = json.loads(run(["docker", "inspect", gateway], capture=True))[0]
        address = info["NetworkSettings"]["Networks"]["neon-kind-support_default"][
            "IPAddress"
        ]
        k(
            "patch",
            "endpointslice",
            "s3-external",
            "--type=json",
            "-p",
            json.dumps(
                [
                    {
                        "op": "replace",
                        "path": "/endpoints/0/addresses/0",
                        "value": address,
                    }
                ]
            ),
        )
        proof["s3_address_after"] = address
        gateway = None
    progress(markers[-1][1])
    proof["wal_after"] = wal_barrier()
    if mode in ("s3", "pageserver-cache"):
        proof["uploaded_checkpoint_after"] = page_checkpoint(
            proof["wal_after"]["frozen_ack_lsn"]
        )
    markers.append(("after", now()))
    progress(markers[-1][1])
    (root / ("stop-" + case)).touch()
    if any(process.wait(timeout=60) != 0 for process in processes):
        raise RuntimeError("live workload failed")
    observations = summarize(
        root / ("fault-" + case + ".jsonl"),
        root / ("reads-" + case + ".jsonl"),
        markers,
        now(),
    )
    if observations["contract_failures"]:
        raise RuntimeError(str(observations["contract_failures"]))
    after = snapshot()
    for name, identity in before.items():
        if name not in excluded and after.get(name) != identity:
            raise RuntimeError("unrelated survivor restarted: " + name)
    proof["unchanged_survivors"] = {
        name: identity for name, identity in before.items() if name not in excluded
    }
    run(["make", "test"], timeout=300)
    result = {
        "case": case,
        "recovery_and_verification_seconds": time.monotonic() - started,
        "proof": proof,
        "observations": observations,
        "reconciliation": json.loads((root / "recovery-history.json").read_text()),
    }
    (root / (case + "-result.json")).write_text(json.dumps(result, indent=2) + "\n")
except Exception as error:
    (root / (case + "-failure.json")).write_text(
        json.dumps({"case": case, "error": str(error), "proof": proof}, indent=2) + "\n"
    )
    raise
finally:
    cleanup_errors = []
    if restore_created:
        try:
            k(
                "delete",
                "statefulset/restore",
                "service/restore",
                "--ignore-not-found",
                "--wait=true",
            )
        except Exception as error:
            cleanup_errors.append(str(error))
    # Submit all restores even if one fails; wait in storage dependency order.
    for name in ("sk0", "sk1", "sk2", "pageserver", "compute", "branch"):
        if name in scaled:
            try:
                k("scale", "statefulset/" + name, "--replicas=1")
            except Exception as error:
                cleanup_errors.append(str(error))
    for name in ("sk0", "sk1", "sk2", "pageserver", "compute", "branch"):
        if name in scaled:
            try:
                k("rollout", "status", "statefulset/" + name, "--timeout=120s")
            except Exception as error:
                cleanup_errors.append(str(error))
    if cleanup_errors:
        (root / (case + "-cleanup-failure.json")).write_text(
            json.dumps(cleanup_errors, indent=2) + "\n"
        )
    if gateway:
        run(["docker", "start", gateway])
        restored = json.loads(run(["docker", "inspect", gateway], capture=True))[0]
        address = restored["NetworkSettings"]["Networks"]["neon-kind-support_default"][
            "IPAddress"
        ]
        k(
            "patch",
            "endpointslice",
            "s3-external",
            "--type=json",
            "-p",
            json.dumps(
                [
                    {
                        "op": "replace",
                        "path": "/endpoints/0/addresses/0",
                        "value": address,
                    }
                ]
            ),
        )
    (root / ("stop-" + case)).touch()
    for process in processes:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
    for log in logs:
        log.close()

    if cleanup_errors and sys.exc_info()[0] is None:
        raise RuntimeError("source restore incomplete: " + str(cleanup_errors))
