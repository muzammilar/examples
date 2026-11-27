"""Bounded per-syscall publication timings with the existing native replica oracle."""

import json
from pathlib import Path
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import control as lab

STAGES = (
    "file_open",
    "file_write",
    "file_fsync",
    "file_close",
    "rename",
    "directory_open",
    "directory_fsync",
    "directory_close",
)


def compression_reference(run):
    program = (
        """import gzip,hashlib,json,time,resource
raw=compressed=0
cpu=wall=0
for seq in range(128):
    body=hashlib.shake_256(('95513:%s:'+str(seq)).encode()).digest(262144)
    start=time.perf_counter_ns(); before=time.process_time_ns()
    packed=gzip.compress(body,compresslevel=1,mtime=0)
    cpu+=time.process_time_ns()-before; wall+=time.perf_counter_ns()-start
    raw+=len(body); compressed+=len(packed)
print(json.dumps({'codec':'gzip-level-1-per-file','uncompressed_bytes':raw,'compressed_bytes':compressed,'ratio':raw/compressed,'compression_cpu_ms':cpu/1e6,'compression_wall_ms':wall/1e6,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'scope':'Independent identical-body compressibility reference in the reader container; not native storage compression'}))"""
        % run
    )
    return json.loads(
        lab.command("exec", "-T", "files-reader", "python3", "-c", program)
    )


def main():
    path = ROOT / "results" / ("publication-stalls-" + str(time.time_ns()))
    path.mkdir()
    lab.save(path / "status.json", {"passed": False})
    index = ROOT / "results/fixture.json"
    fixture = json.loads(index.read_text())
    if fixture["stage"] != "ready":
        raise RuntimeError("Require the parent's verified fixture")
    run = uuid.uuid4().hex
    fixture["runs"].append(run)
    lab.save(index, fixture)
    output = lab.execute("writer", "seed", run, 128, 262144)
    (path / "publication.log").write_text(output)
    acks = [
        row for row in map(json.loads, output.splitlines()) if row.get("event") == "ack"
    ]
    if len(acks) != 128 or any(
        set(row.get("stages_ms", {})) != set(STAGES) for row in acks
    ):
        raise RuntimeError("Expected 128 complete instrumented publications")
    result = {"run": run, "files": 128, "payload_bytes": 262144, "stages": {}}
    for stage in STAGES:
        values = sorted(row["stages_ms"][stage] for row in acks)
        result["stages"][stage] = {
            "mean_ms": statistics.mean(values),
            "p50_ms": values[63],
            "p95_ms": values[121],
            "max_ms": values[-1],
            "over_500ms": sum(value > 500 for value in values),
        }
    result["long_publications"] = [
        {
            "sequence": row["sequence"],
            "duration_ms": row["duration_ms"],
            "stages_ms": row["stages_ms"],
        }
        for row in acks
        if row["duration_ms"] > 500
    ]
    result["native_verify"] = lab.audit(path)
    result["compression_reference"] = compression_reference(run)
    result["limits"] = [
        "Stage durations localize blocking syscalls; they do not isolate mount/kernel/server/network internals.",
        "One bounded diagnostic, not a new competitive benchmark; original failed large comparison remains unchanged.",
        "Instrumentation adds clock/JSON bookkeeping; no isolated overhead claim.",
    ]
    lab.save(path / "result.json", result)
    lab.save(path / "status.json", {"passed": True})
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
