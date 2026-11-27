"""Run native Spark applications with independent live/fresh readers."""
import copy
import datetime
import hashlib
import json
import sys
import time

import kube
import events


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def wait_job(name, directory, timeout=360):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        obj = kube.get("job", name)
        logs = kube.job_logs(name)
        (directory / (name + ".log")).write_text(logs)
        if obj and obj.get("status", {}).get("failed", 0):
            save(directory / (name + "-failed.json"), obj)
            raise RuntimeError(f"Job {name} failed; preserve {directory}")
        if obj and obj.get("status", {}).get("succeeded", 0):
            save(directory / (name + "-completed.json"), obj)
            return logs
        time.sleep(1)
    raise TimeoutError(f"Job {name} did not finish; evidence: {directory}")


def setup():
    code = ('import json,urllib.request; x=json.load(urllib.request.urlopen('
            '"http://namenode:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState",timeout=3)); '
            'print(x["beans"][0]["NumLiveDataNodes"])')
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = kube.run("exec", "hdfs-tools", "--", "python3", "-c", code, check=False)
        if result.returncode == 0 and result.stdout.strip() == "3":
            break
        time.sleep(1)
    else:
        raise TimeoutError("Three DataNodes did not register")
    kube.run("exec", "hdfs-tools", "--", "hdfs", "dfsadmin", "-safemode", "wait", timeout=120)
    for args in (("-mkdir", "-p", "/lab/events"), ("-chown", "spark:spark", "/lab", "/lab/events"),
                 ("-chmod", "0755", "/lab", "/lab/events")):
        kube.run("exec", "hdfs-tools", "--", "hdfs", "dfs", *args)
    print("Three DataNodes registered; owned paths ready without input reload")


def application(name, directory, inject=False):
    started = time.monotonic()
    fault = None
    with (directory / (name + "-history.jsonl")).open("x") as history:
        while time.monotonic() - started < 360:
            obj = kube.get("sparkapplication", name)
            if obj is None:
                raise RuntimeError(f"Missing application {name}")
            observed = kube.sample()
            record = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "elapsed_s": time.monotonic() - started, "application": obj, **observed}
            drivers = [p for p in observed["pods"] if p["metadata"].get("labels", {}).get("spark-role") == "driver"
                       and any(o["uid"] == obj["metadata"]["uid"] for o in p["metadata"].get("ownerReferences", []))]
            live_logs = kube.job_logs("orders-live-reader")
            record["live_reader_log"] = live_logs
            (directory / "live-reader.log").write_text(live_logs)
            if len(drivers) == 1:
                driver = drivers[0]
                driver_name = driver["metadata"]["name"]
                logs = kube.run("logs", driver_name, check=False).stdout
                (directory / (name + "-driver.log")).write_text(logs)
                if inject and fault is None and driver.get("status", {}).get("phase") == "Running" and driver["status"].get("podIP"):
                    code = ('import json,urllib.request; base=' + repr("http://" + driver["status"]["podIP"] + ":4040/api/v1") + '; '
                            'apps=json.load(urllib.request.urlopen(base+"/applications",timeout=2)); '
                            'print(json.dumps(json.load(urllib.request.urlopen(base+"/applications/"+apps[0]["id"]+"/executors",timeout=2))))')
                    probe = kube.run("exec", "hdfs-tools", "--", "python3", "-c", code, check=False, timeout=10)
                    record["driver_rest_probe"] = {"code": probe.returncode, "body": probe.stdout, "error": probe.stderr}
                    if probe.returncode == 0 and probe.stdout.strip().startswith("["):
                        executors = json.loads(probe.stdout)
                        active = {str(e["id"]) for e in executors if e["id"] != "driver" and e["activeTasks"] > 0}
                        for pod in observed["pods"]:
                            labels = pod["metadata"].get("labels", {})
                            if labels.get("spark-role") != "executor" or labels.get("spark-exec-id") not in active:
                                continue
                            if not any(o["uid"] == driver["metadata"]["uid"] for o in pod["metadata"].get("ownerReferences", [])):
                                continue
                            fault = {"pod": pod["metadata"]["name"], "uid": pod["metadata"]["uid"],
                                     "executor_id": labels["spark-exec-id"], "elapsed_s": time.monotonic() - started}
                            kube.run("delete", "pod", fault["pod"], "--force", "--grace-period=0", "--wait=true")
                            record["injection"] = fault
                            break
                phase = driver.get("status", {}).get("phase")
                if phase in ("Succeeded", "Failed"):
                    statuses = driver["status"].get("containerStatuses", [])
                    exits = [s.get("state", {}).get("terminated", {}).get("exitCode") for s in statuses]
                    record["driver_exit_codes"] = exits
                    if phase == "Failed" or not exits or any(code != 0 for code in exits):
                        history.write(json.dumps(record) + "\n")
                        raise RuntimeError(f"Native driver failed: {directory}")
                    states = [obj.get("status", {}).get("currentState", {})]
                    states += list(obj.get("status", {}).get("stateTransitionHistory", {}).values())
                    if any(state.get("currentStateSummary") == "Succeeded" for state in states):
                        history.write(json.dumps(record) + "\n")
                        if inject and fault is None:
                            raise RuntimeError("Application completed without a proven active-task fault")
                        expected = hashlib.sha256((kube.ROOT / "orders-workload/orders.py").read_bytes()).hexdigest()
                        if "WORKLOAD_SOURCE_SHA256 " + expected not in logs:
                            raise RuntimeError("Deployed workload does not match repository source")
                        result = {"application": name, "native_success": True, "driver_exit_codes": exits,
                                  "fault": fault, "elapsed_s": time.monotonic() - started,
                                  "source_sha256": expected}
                        save(directory / (name + "-result.json"), result)
                        return result
            history.write(json.dumps(record) + "\n")
            history.flush()
            time.sleep(1)
    raise TimeoutError(f"Application {name} did not qualify within 360 seconds")


def verify(directory):
    writer = kube.get("sparkapplication", "orders-write")
    if writer is None:
        raise RuntimeError("No existing writer; never reseed during verification")
    name = "orders-read-" + str(time.time_ns())
    spec = copy.deepcopy(writer["spec"])
    spec["driverArgs"] = ["verify"]
    for key in ("spark.app.id", "spark.kubernetes.driver.pod.name", "spark.kubernetes.executor.podNamePrefix"):
        spec["sparkConf"].pop(key, None)
    kube.create({"apiVersion": "spark.apache.org/v1", "kind": "SparkApplication",
                 "metadata": {"name": name}, "spec": spec})
    application(name, directory)
    template = json.loads(kube.run("create", "--dry-run=client", "-f", str(kube.ROOT / "deploy/verifier.yaml"), "-o", "json").stdout)
    template["metadata"].pop("name", None)
    template["metadata"]["generateName"] = "parquet-verifier-"
    job = kube.create(template)
    logs = wait_job(job["metadata"]["name"], directory, timeout=180)
    values = [json.loads(line) for line in logs.splitlines() if line.startswith('{"verifier_source_sha256":')]
    if len(values) != 1:
        raise ValueError("Missing unique independent verification result")
    result = values[0]
    if result["verifier_source_sha256"] != hashlib.sha256((kube.ROOT / "parquet-verifier/verify.py").read_bytes()).hexdigest():
        raise ValueError("Deployed oracle source does not match")
    save(directory / "independent-readback.json", result)
    print(json.dumps({"verification": "passed", "orders": result["orders"], "evidence": str(directory)}))


def main():
    mode = sys.argv[1]
    if mode == "setup":
        setup()
        return
    if mode not in ("test", "executor-recovery", "verify"):
        raise ValueError(mode)
    directory = kube.ROOT / "results" / f"{mode}-{time.time_ns()}"
    directory.mkdir(parents=True)
    namespace = kube.get("namespace", "spark-lab")
    if mode != "verify":
        if kube.get("sparkapplication", "orders-write") is None:
            kube.run("apply", "-f", str(kube.ROOT / "deploy/live-reader.yaml"))
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if any(p["metadata"].get("labels", {}).get("job-name") == "orders-live-reader" and p.get("status", {}).get("phase") == "Running" for p in kube.pods()):
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Live reader did not start")
            kube.run("apply", "-f", str(kube.ROOT / "deploy/orders.yaml"))
        application("orders-write", directory, inject=mode == "executor-recovery")
        if mode == "executor-recovery":
            events.verify(directory)
        reader_logs = wait_job("orders-live-reader", directory)
        reader_events = [json.loads(line) for line in reader_logs.splitlines() if line.startswith("{")]
        expected_reader = hashlib.sha256((kube.ROOT / "parquet-verifier/live_reader.py").read_bytes()).hexdigest()
        if not any(event.get("event") == "reader_started" and event.get("source_sha256") == expected_reader for event in reader_events):
            raise ValueError("Deployed live-reader source does not match")
    writer = kube.get("sparkapplication", "orders-write")
    save(directory / "run-environment.json", {"namespace_uid": namespace["metadata"]["uid"],
                                            "writer_uid": writer["metadata"]["uid"],
                                            "context": "kind-spark-kubernetes-operator"})
    verify(directory)


if __name__ == "__main__":
    main()
