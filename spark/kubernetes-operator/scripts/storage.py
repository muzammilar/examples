"""Verify every physical HDFS replica, then replace only one fenced owned disk."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import kube


class PendingReplicas(RuntimeError):
    pass


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def expected_manifest():
    namespace = kube.get("namespace", "spark-lab")["metadata"]["uid"]
    writer = kube.get("sparkapplication", "orders-write")["metadata"]["uid"]
    for path in sorted((kube.ROOT / "results").glob("*/independent-readback.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        env = path.parent / "run-environment.json"
        if not env.exists():
            continue
        metadata = json.loads(env.read_text())
        if metadata["namespace_uid"] == namespace and metadata["writer_uid"] == writer:
            return {f["file"]: f for f in json.loads(path.read_text())["file_manifest"]}
    raise RuntimeError("No fresh independent oracle for this namespace/writer; never reseed during audit")


def audit(directory, copies=3, wait_s=90):
    expected = expected_manifest()
    deadline = time.monotonic() + wait_s
    while True:
        fsck = kube.run("exec", "hdfs-tools", "--", "hdfs", "fsck", "/lab/orders", "-files", "-blocks", "-locations", check=False)
        stamp = str(time.time_ns())
        (directory / ("fsck-" + stamp + ".txt")).write_text(fsck.stdout + fsck.stderr)
        files = {}
        current = None
        for line in fsck.stdout.splitlines():
            match = re.match(r"(/\S+\.parquet)\s+(\d+) bytes", line)
            if match:
                path = Path(match[1])
                current = path.name if path.parent.as_posix() == "/lab/orders" else None
                if current:
                    files[current] = {"bytes": int(match[2]), "blocks": []}
            elif line.startswith("/"):
                current = None
            block = re.search(r"\b(blk_-?\d+)_\d+\s+len=(\d+)\s+Live_repl=(\d+)", line)
            if block and current:
                files[current]["blocks"].append({"id": block[1], "bytes": int(block[2]), "live_replicas": int(block[3])})
        try:
            if fsck.returncode != 0 or "Status: HEALTHY" not in fsck.stdout:
                raise RuntimeError("Native fsck is not healthy; preserve evidence")
            if set(files) != set(expected):
                raise ValueError(f"Wrong published file set or unrecognized fsck format: {set(files) ^ set(expected)}")
            if any(not f["blocks"] for f in files.values()):
                raise ValueError("Missing block information for a nonempty Parquet file")
            if any(b["live_replicas"] != copies for f in files.values() for b in f["blocks"]):
                raise PendingReplicas(f"Native live copies have not converged to {copies}")
            nodes = [p for p in kube.pods() if p["metadata"].get("labels", {}).get("app") == "hdfs-datanode" and p.get("status", {}).get("phase") == "Running"]
            if len(nodes) != copies or len({p["spec"]["nodeName"] for p in nodes}) != copies:
                raise PendingReplicas("Live DataNodes are not independently placed on the expected worker count")
            physical = {}
            script = r'''import hashlib,json,re
from pathlib import Path
files = FILES
blocks = {p.name:p for p in Path('/data/data/current').rglob('blk_*') if p.is_file() and 'finalized' in p.parts and re.fullmatch(r'blk_-?\d+',p.name)}
result = {}
for name,info in files.items():
    digest = hashlib.sha256()
    missing = []
    for block in info['blocks']:
        path = blocks.get(block['id'])
        if path is None:
            missing.append(block['id']); continue
        if path.stat().st_size != block['bytes']:
            raise ValueError('Wrong finalized block length: '+str(path))
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
    result[name] = {'sha256':digest.hexdigest(),'missing':missing}
print(json.dumps(result))
'''.replace("FILES", repr(files))
            for pod in nodes:
                name = pod["metadata"]["name"]
                body = json.loads(kube.run("exec", name, "--", "python3", "-c", script).stdout)
                physical[name] = {"uid": pod["metadata"]["uid"], "worker": pod["spec"]["nodeName"], "files": body}
            save(directory / ("physical-" + stamp + ".json"), physical)
            for node in physical.values():
                for name, observed in node["files"].items():
                    if observed["missing"]:
                        raise PendingReplicas("A new DataNode has not finalized every required block")
                    if observed["sha256"] != expected[name]["sha256"] or files[name]["bytes"] != expected[name]["bytes"]:
                        raise ValueError("Physical replica differs from the independently decoded Parquet body")
            result = {"copies": copies, "parquet_files": len(files), "blocks": sum(len(f["blocks"]) for f in files.values()),
                      "file_bytes_per_copy": sum(f["bytes"] for f in files.values()),
                      "all_replica_file_hashes_match_oracle": True, "physical": physical}
            save(directory / f"replica-verdict-{copies}.json", result)
            return result
        except PendingReplicas as error:
            if time.monotonic() >= deadline:
                raise
            save(directory / ("converging-" + stamp + ".json"), {"reason": str(error)})
            time.sleep(2)


def wait_live(count, timeout=60):
    deadline = time.monotonic() + timeout
    code = ('import json,urllib.request; x=json.load(urllib.request.urlopen('
            '"http://namenode:9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState",timeout=3)); '
            'print(x["beans"][0]["NumLiveDataNodes"])')
    while time.monotonic() < deadline:
        result = kube.run("exec", "hdfs-tools", "--", "python3", "-c", code, check=False)
        if result.returncode == 0 and result.stdout.strip() == str(count):
            return
        time.sleep(1)
    raise TimeoutError(f"Expected {count} live DataNodes")


def replace(directory):
    audit(directory)
    claim = "blocks-datanode-2"
    old_claim = kube.get("pvc", claim)
    old_version = kube.run("exec", "datanode-2", "--", "cat", "/data/data/current/VERSION").stdout
    old_nn = kube.run("exec", "namenode-0", "--", "cat", "/data/name/current/VERSION").stdout
    save(directory / "before.json", {"pvc": old_claim, "datanode_version": old_version, "namenode_version": old_nn,
                                    "resources": kube.sample()})
    started = time.monotonic()
    try:
        kube.run("scale", "statefulset", "datanode", "--replicas=2")
        kube.run("wait", "--for=delete", "pod/datanode-2", "--timeout=120s", timeout=135)
        wait_live(2)
        audit(directory, copies=2)
        subprocess.run([sys.executable, str(kube.ROOT / "scripts/lifecycle.py"), "verify"], cwd=kube.ROOT, env=kube.ENV, check=True)
        # Both surviving complete copies were proved before discarding the fenced disk.
        kube.run("delete", "pvc", claim, "--wait=true", "--timeout=120s", timeout=135)
        kube.run("scale", "statefulset", "datanode", "--replicas=3")
        kube.run("rollout", "status", "statefulset/datanode", "--timeout=180s", timeout=195)
        wait_live(3)
        new_claim = kube.get("pvc", claim)
        new_version = kube.run("exec", "datanode-2", "--", "cat", "/data/data/current/VERSION").stdout
        if new_claim["metadata"]["uid"] == old_claim["metadata"]["uid"] or new_claim["spec"]["volumeName"] == old_claim["spec"]["volumeName"]:
            raise ValueError("No actual replacement volume observed")
        if new_claim["spec"].get("dataSource") or new_claim["spec"].get("dataSourceRef"):
            raise ValueError("Replacement unexpectedly has a populated source")
        old_id = re.search(r"^datanodeUuid=(.+)$", old_version, re.M)
        new_id = re.search(r"^datanodeUuid=(.+)$", new_version, re.M)
        if not old_id or not new_id or old_id[1] == new_id[1]:
            raise ValueError("Expected a new native DataNode identity")
        if kube.run("exec", "namenode-0", "--", "cat", "/data/name/current/VERSION").stdout != old_nn:
            raise ValueError("NameNode namespace identity changed")
        audit(directory)
        subprocess.run([sys.executable, str(kube.ROOT / "scripts/lifecycle.py"), "verify"], cwd=kube.ROOT, env=kube.ENV, check=True)
        save(directory / "replacement-result.json", {"passed": True, "elapsed_s": time.monotonic() - started,
             "old_pvc_uid": old_claim["metadata"]["uid"], "new_pvc_uid": new_claim["metadata"]["uid"],
             "old_datanode_uuid": old_id[1], "new_datanode_uuid": new_id[1],
             "namenode_identity_unchanged": True, "input_reloaded": False, "final_resources": kube.sample()})
    finally:
        kube.run("scale", "statefulset", "datanode", "--replicas=3")


def main():
    mode = sys.argv[1]
    directory = kube.ROOT / "results" / f"storage-{mode}-{time.time_ns()}"
    directory.mkdir(parents=True)
    if mode == "audit":
        result = audit(directory)
        print(json.dumps({"audit": "passed", "copies": result["copies"], "files": result["parquet_files"], "evidence": str(directory)}))
    elif mode == "replace":
        replace(directory)
        print(json.dumps({"empty_disk_replacement": "passed", "evidence": str(directory)}))
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
