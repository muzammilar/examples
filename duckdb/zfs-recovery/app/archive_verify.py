"""Read back retained native generations before the lab's volumes are deleted."""

import csv
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import signal
import traceback
import time
import uuid

import history
import lab


PROCESS_PROBE = r"""import json,os,signal,time
from pathlib import Path
def observe(pid):
 try:
  p=Path('/proc')/str(pid); fields=p.joinpath('stat').read_text().split(') ',1)[1].split()
  return {'pid':pid,'start_ticks':fields[19],'state':fields[0],'argv':[v.decode() for v in p.joinpath('cmdline').read_bytes().split(b'\0') if v]}
 except FileNotFoundError:return None
records=[value for p in Path('/proc').glob('[0-9]*') if (value:=observe(int(p.name)))]
if not records:raise RuntimeError('native process listing unavailable')
helpers=[v for v in records if len(v['argv'])>1 and v['argv'][1].startswith('/tmp/ducklab-archive-')]
servers=[v for v in records if len(v['argv'])>1 and v['argv'][1]=='/opt/ducklab/server.py']
selected=[v for v in helpers if v['argv']==expected] if expected else []
if len(selected)>1:raise RuntimeError('ambiguous native helper identity')
actions=[]
if terminate:
 for before in selected:
  try:
   descriptor=os.pidfd_open(before['pid'])
  except ProcessLookupError:continue
  try:
   current=observe(before['pid'])
   if current is None:continue
   if current['start_ticks']!=before['start_ticks'] or current['argv']!=before['argv']:raise RuntimeError('native helper identity changed')
   try:signal.pidfd_send_signal(descriptor,signal.SIGTERM)
   except ProcessLookupError:pass
  finally:os.close(descriptor)
  deadline=time.monotonic()+10
  while time.monotonic()<deadline:
   after=observe(before['pid'])
   if after is None or after['start_ticks']!=before['start_ticks'] or after['state']=='Z':break
   time.sleep(.1)
  else:raise RuntimeError('native helper failed to stop')
  actions.append({'before':before,'after':after,'signal':'SIGTERM via pidfd'})
print(json.dumps({'helpers':helpers,'servers':servers,'termination':actions,'healthy_proc_listing':bool(records),'expected':expected,'fenced':terminate}))
"""


def native_processes(node, expected=None, terminate=False):
    code = (
        "expected="
        + repr(expected)
        + ";terminate="
        + repr(terminate)
        + "\n"
        + PROCESS_PROBE
    )
    return json.loads(
        lab.ssh(node, shlex.join(["/opt/venv/bin/python", "-c", code]), timeout=20)
    )


def fences():
    observations = {}
    for node in lab.NODES:
        processes = native_processes(node)
        pid = lab.ssh(node, "systemctl show ducklab.service -p MainPID --value")
        active = lab.ssh(node, "systemctl is-active ducklab.service || true")
        if (
            pid != "0"
            or active != "inactive"
            or processes["helpers"]
            or processes["servers"]
        ):
            raise RuntimeError("native owner or archive helper remains active: " + node)
        observations[node] = {
            "main_pid": pid,
            "service": active,
            "processes": processes,
            "boot_id": lab.ssh(node, "cat /proc/sys/kernel/random/boot_id"),
        }
    return {"passed": True, "nodes": observations}


def native_job(root, token, arguments, timeout=600):
    mode = arguments[2]
    history.save(
        root / ("native-job-" + token + "-" + mode + ".json"),
        {"argv": arguments, "utc_ns": time.time_ns()},
    )
    try:
        return json.loads(lab.ssh("primary", shlex.join(arguments), timeout=timeout))
    except BaseException:
        failure = {"error": traceback.format_exc(), "argv": arguments}
        try:
            failure["fencing"] = native_processes("primary", arguments, terminate=True)
        except BaseException:
            failure["fencing_error"] = traceback.format_exc()
        history.save(root / ("native-failure-" + token + "-" + mode + ".json"), failure)
        raise


def transfer(source, destination):
    command = [
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
        source,
        destination,
    ]
    subprocess.run(command, check=True, timeout=300)


def native_clone_exports(root, dataset, tables):
    token = uuid.uuid4().hex
    snapshot = dataset + "@verify-" + token
    clone = "ducklab/verify-" + token
    path = "/srv/ducklab/verify-" + token + "/events.duckdb"
    output = "/tmp/ducklab-export-" + token
    local_output = root / token
    local_output.mkdir()
    helper_directory = "/tmp/ducklab-archive-" + token
    lab.ssh("primary", "install -d -o lab -g lab " + helper_directory)
    for module in ("archive_guest.py", "export_csv.py"):
        transfer(
            "/opt/lab/app/" + module, "lab@primary:" + helper_directory + "/" + module
        )
    helper = helper_directory + "/archive_guest.py"
    lab.ssh(
        "primary",
        f"zfs snapshot {snapshot}; zfs clone -o readonly=off {snapshot} {clone}",
    )
    recovered = native_job(
        root, token, ["/opt/venv/bin/python", helper, "recover", path, output]
    )
    if set(recovered["tables"]) != set(tables):
        raise RuntimeError(
            "unwitnessed native tables in " + dataset + ": " + repr(recovered["tables"])
        )
    lab.ssh("primary", "zfs set readonly=on " + clone)
    exported = native_job(
        root, token, ["/opt/venv/bin/python", helper, "export", path, output, *tables]
    )
    expected_sources = {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in ("archive_guest.py", "export_csv.py")
    }
    for observation in (recovered, exported):
        if (
            observation["source_sha256"] != expected_sources
            or observation["duckdb_version"] != lab.duckdb.__version__
        ):
            raise RuntimeError(
                "actual guest helper/engine differs from controller artifacts"
            )
    files = {}
    for table in tables:
        target = local_output / (table + ".csv")
        transfer("lab@primary:" + output + "/" + table + ".csv", str(target))
        with target.open("rb") as stream:
            os.fsync(stream.fileno())
        files[table] = target
    return files, {
        "dataset": dataset,
        "snapshot": snapshot,
        "read_only_clone": clone,
        "native_recovery": recovered,
        "native_export": exported,
        "scope": "Original retained dataset is never opened writable. Only a fresh clone is recovered/checkpointed, then exported read-only. Clone/snapshot retained through teardown; this is not an independent backup.",
    }


def standby_export(root):
    path = root / "promoted-standby.csv"
    with lab.connect("standby") as connection, path.open("x", newline="") as stream:
        result = connection.execute(
            "CALL quack_query('quack:standby:9494','SELECT * FROM events ORDER BY event_id',token := 'local-lab-token',disable_ssl := true)"
        )
        writer = csv.writer(stream)
        writer.writerow([column[0] for column in result.description])
        while rows := result.fetchmany(2048):
            writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def main():
    root = lab.RESULTS / ("archive-verification-" + uuid.uuid4().hex)
    root.mkdir()
    history.save(
        root / "started.json",
        {
            "utc_ns": time.time_ns(),
            "source_sha256": {
                name: hashlib.sha256(
                    (Path(__file__).parent / name).read_bytes()
                ).hexdigest()
                for name in (
                    "archive_verify.py",
                    "archive_guest.py",
                    "export_csv.py",
                    "history.py",
                    "lab.py",
                )
            },
        },
    )
    try:
        for node in lab.NODES:
            observed = native_processes(node)
            history.save(root / (node + "-native-processes-before.json"), observed)
            if observed["helpers"] or (node == "primary" and observed["servers"]):
                raise RuntimeError(
                    "an earlier helper or old-primary server must be fenced before export: "
                    + node
                )
        witnesses = history.witness_hashes(lab.RESULTS)
        if (
            lab.ssh("primary", "systemctl show ducklab.service -p MainPID --value")
            != "0"
            or lab.ssh("primary", "systemctl is-active ducklab.service || true")
            != "inactive"
        ):
            raise RuntimeError("the old primary's writable server must remain fenced")
        lab.ssh("standby", "test -f /var/lib/ducklab-promoted")
        lab.ssh(
            "primary",
            "if ! zpool list ducklab >/dev/null 2>&1; then zpool import -d /dev/disk/by-id ducklab; fi",
        )
        lab.healthy_pool("primary")
        raw = lab.ssh("primary", "zfs get -Hpr -o name,value guid ducklab")
        datasets = {
            name: guid
            for line in raw.splitlines()
            for name, guid in [line.split()]
            if "@" not in name
            and (name == "ducklab/data" or name.startswith("ducklab/retained-data-"))
        }
        groups = {}
        for path in sorted((lab.RESULTS / "live").glob("*/generation.json")):
            guid = json.loads(path.read_text())["dataset_guid"]
            selected = [
                name for name, candidate in datasets.items() if candidate == guid
            ]
            if len(selected) != 1:
                raise RuntimeError(
                    "historical dataset missing or ambiguous: " + path.parent.name
                )
            groups.setdefault(selected[0], []).append(path.parent)
        rows, native, csv_files = [], [], {}
        # Inventory every retained generation, not only those with UUID manifests.
        # An unknown nonempty generation cannot be silently discarded at cleanup.
        inventory = {
            node: lab.ssh(
                node,
                "zfs list -Hp -o name,guid,used,mountpoint -r ducklab; find /srv/ducklab -name events.duckdb -o -name events.duckdb.wal",
            )
            for node in lab.NODES
        }
        disk_guid = json.loads((lab.RESULTS / "disk-test.json").read_text())[
            "dataset_guid"
        ]
        disk_datasets = [name for name, guid in datasets.items() if guid == disk_guid]
        if len(disk_datasets) != 1:
            raise RuntimeError("retained disk-test dataset missing")
        groups.setdefault(disk_datasets[0], [])
        for dataset, cases in groups.items():
            tables = [
                json.loads((case / "config.json").read_text())["table"]
                for case in cases
            ]
            # The legacy disk workload shares the generation containing the
            # latest continuous crash case; its original ACKs must survive too.
            disk_generation = datasets[dataset] == disk_guid
            if disk_generation:
                tables.append("events")
            files, observation = native_clone_exports(root, dataset, tables)
            native.append(observation)
            for case in cases:
                config = json.loads((case / "config.json").read_text())
                proof = history.reconcile(files[config["table"]], case, config)
                expected = json.loads((case / "result.json").read_text())["proof"]
                if proof != {
                    key: value for key, value in expected.items() if key != "gzip1"
                }:
                    raise RuntimeError("historical body differs: " + case.name)
                rows.append(
                    {
                        "case": case.name,
                        "proof": proof,
                        "configuration_failed": (
                            case / "configuration-drift.json"
                        ).exists(),
                    }
                )
                csv_files[case.name] = str(files[config["table"]].relative_to(root))
            if disk_generation:
                disk = history.reconcile_legacy(
                    files["events"],
                    lab.RESULTS,
                    ["disk"],
                    lab.integer("CUSTOMERS", 10000),
                )
                if disk["rows_verified"] != lab.integer("ROWS", 2000000):
                    raise RuntimeError(
                        "disk workload lost rows during later operations"
                    )
                rows.append({"case": "legacy-disk", "proof": disk})
                csv_files["legacy-disk"] = str(files["events"].relative_to(root))
        for dataset in sorted(set(datasets) - set(groups) - {"ducklab/data"}):
            mount = "/srv/ducklab/" + dataset.split("/", 1)[1]
            has_database = lab.ssh(
                "primary",
                "if test -f "
                + mount
                + "/events.duckdb; then echo present; else echo empty; fi",
            )
            if has_database == "present":
                files, observation = native_clone_exports(root, dataset, [])
                native.append(observation)
            else:
                native.append(
                    {
                        "dataset": dataset,
                        "database": "absent",
                        "scope": "Empty retained generation inventoried; no DuckDB body to reconcile.",
                    }
                )
        standby_retained = lab.ssh(
            "standby",
            "find /srv/ducklab -path '*/retained-data-*/events.duckdb' -print",
        )
        if standby_retained:
            raise RuntimeError(
                "unwitnessed retained standby database: " + standby_retained
            )
        source_files, observation = native_clone_exports(
            root, "ducklab/data", ["events"]
        )
        native.append(observation)
        source = history.reconcile_legacy(
            source_files["events"],
            lab.RESULTS,
            ["seed", "live", "fault"],
            lab.integer("CUSTOMERS", 10000),
            error_labels=["fault"],
        )
        csv_files["fenced-primary-source"] = str(
            source_files["events"].relative_to(root)
        )
        labels = ["seed", "live", "fault", "promoted"]
        if list(lab.RESULTS.glob("benchmark-*.jsonl")):
            labels.append("benchmark")
        destination = history.reconcile_legacy(
            standby_export(root),
            lab.RESULTS,
            labels,
            lab.integer("CUSTOMERS", 10000),
            missing_labels=["fault"],
        )
        if history.witness_hashes(lab.RESULTS) != witnesses:
            raise RuntimeError("workload histories changed during final verification")
        csv_files["promoted-standby"] = "promoted-standby.csv"
        history.save(
            root / "result.json",
            {
                "passed": True,
                "parameters": {
                    "customers": lab.integer("CUSTOMERS", 10000),
                    "rows": lab.integer("ROWS", 2000000),
                },
                "history_sha256": witnesses,
                "csv_files": csv_files,
                "historical_cases": rows,
                "fenced_primary_source": source,
                "promoted_standby": destination,
                "native": native,
                "dataset_guid_by_name": datasets,
                "all_native_generation_inventory": inventory,
                "scope": "Every earlier UUID body and legacy disk ACK survives the whole sequence. Source-tail ACKs absent on the asynchronous destination are retained in the exported fenced source, not silently destroyed at cleanup. Read-only clones are verification copies on the same pool, not independent backups.",
            },
        )
        print(str(root), flush=True)
    except BaseException as error:
        history.save(root / "failure.json", {"passed": False, "error": repr(error)})
        raise


if __name__ == "__main__":

    def stopped(*_):
        raise KeyboardInterrupt("stop requested")

    signal.signal(signal.SIGTERM, stopped)
    main()
