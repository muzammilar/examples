"""Native cross-container journal writer exclusion while the primary stays live."""

import json
import subprocess
import time

import experiment as bench
import ingestion
from journal import inspect_owned


PROBE = r"""
import json, pathlib, resource, subprocess, time
started = time.monotonic_ns()
accepted = 0
for attempt in range(10):
    begin = time.monotonic_ns()
    outcome = subprocess.run(['/opt/rayforce/rayforce', '-c', '1', '-m', '128M', '-L', '/state/journal', '-f', '/state/lock-probe.rfl'], capture_output=True, text=True, timeout=4)
    rejected = outcome.returncode != 0 and 'open in another process' in outcome.stderr
    print('__ATTEMPT__'+json.dumps({'attempt': attempt, 'elapsed_ns': time.monotonic_ns()-begin, 'returncode': outcome.returncode, 'rejected_by_lock': rejected, 'stdout': outcome.stdout, 'stderr': outcome.stderr}), flush=True)
    accepted += not rejected
    if not rejected:
        break
    time.sleep(0.5)
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
print('__FENCING_METRICS__'+json.dumps({'passed': accepted == 0, 'wall_ns': time.monotonic_ns()-started,
 'process_cpu_core_seconds': usage.ru_utime+usage.ru_stime,
 'process_peak_rss_bytes': usage.ru_maxrss*1024,
 'cgroup': {name: pathlib.Path('/sys/fs/cgroup', name).read_text() for name in ('cpu.stat','memory.peak','memory.events','io.stat')}}), flush=True)
raise SystemExit(0 if accepted == 0 else 1)
"""


def probe(compose, env, state, root, server, witness):
    before = inspect_owned(server)
    if env.get("JOURNAL_VOLUME"):
        subprocess.run(
            [
                "docker",
                "exec",
                server,
                "python",
                "-c",
                "from pathlib import Path; Path('/state/lock-probe.rfl').write_text('(exit 0)\\n')",
            ],
            check=True,
            timeout=30,
        )
    else:
        (state / "lock-probe.rfl").write_text("(exit 0)\n")
    name = "rayforce-journal-contender-" + root.name[:8]
    samples = []
    launched = False
    try:
        subprocess.run(
            compose
            + ["run", "-d", "--no-deps", "--name", name, "contender", "-c", PROBE],
            env=env,
            check=True,
            timeout=60,
        )
        launched = True
        endpoint = bench.docker_socket()
        deadline = time.monotonic() + 60
        while True:
            inspection = inspect_owned(name)
            if not inspection["State"]["Running"]:
                break
            sample = bench.resource_sample(endpoint, inspection["Id"])
            if sample is not None:
                samples.append(sample)
            if time.monotonic() > deadline:
                raise TimeoutError("native writer-lock probes exceeded 60 seconds")
            time.sleep(0.1)
        ingestion.save(root / "contender-inspect.json", inspection)
        ingestion.save(root / "contender-samples.json", samples)
        raw = bench.capture(
            ["docker", "logs", name], text=True, stderr=subprocess.STDOUT
        )
        (root / "contender.log").write_text(raw)
        attempts = [
            json.loads(line.removeprefix("__ATTEMPT__"))
            for line in raw.splitlines()
            if line.startswith("__ATTEMPT__")
        ]
        metrics = [
            json.loads(line.removeprefix("__FENCING_METRICS__"))
            for line in raw.splitlines()
            if line.startswith("__FENCING_METRICS__")
        ]
        if (
            inspection["State"]["ExitCode"]
            or len(attempts) != 10
            or not all(a["rejected_by_lock"] for a in attempts)
            or len(metrics) != 1
            or not metrics[0]["passed"]
        ):
            raise RuntimeError(
                "second native writer was not rejected by the actual journal lock"
            )
        config = inspection["HostConfig"]
        if (
            config["NanoCpus"] != 10**9
            or config["Memory"] != 512 * 1024**2
            or config["MemorySwap"] != 512 * 1024**2
        ):
            raise RuntimeError("contender effective resource limits differ")
        if not any(
            m["Destination"] == "/state"
            and m["RW"]
            and (
                m.get("Name") == env["JOURNAL_VOLUME"]
                if env.get("JOURNAL_VOLUME")
                else m["Source"] == str(state)
            )
            for m in inspection["Mounts"]
        ):
            raise RuntimeError("contender did not access the same owned native journal")
        after = inspect_owned(server)
        if not after["State"]["Running"] or any(
            after["State"][key] != before["State"][key] for key in ("Pid", "StartedAt")
        ):
            raise RuntimeError("primary exited/restarted during writer-lock probes")
        result = {
            "passed": True,
            "attempts": attempts,
            "native": metrics[0],
            "sampled_resources": bench.resource_summary(samples),
            "image_id": inspection["Image"],
            "same_primary_generation": True,
            "scope": "Ten native process opens from another container against the exact same journal storage. Exclusive-open rejection, not primary promotion or cross-host lease fencing.",
        }
        ingestion.save(root / "fencing-proof.json", result)
        witness.add(
            kind="writer-exclusion",
            phase="writer-lock",
            attempts=len(attempts),
            rejected=True,
        )
    finally:
        if launched:
            info = inspect_owned(name)
            if info["State"]["Running"]:
                subprocess.run(["docker", "kill", name], check=True, timeout=30)
            subprocess.run(["docker", "rm", name], check=True, timeout=30)
