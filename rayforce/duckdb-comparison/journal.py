"""Native journal capability gate; live workload/fault qualification follows it."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
import uuid

import experiment as bench
import ingestion


class NativeIPC:
    """One bounded native connection, no automatic replay of failed requests."""

    def __init__(self, compose, env, name, log, timeout=12):
        if not 1 <= timeout <= 240:
            raise ValueError("native IPC deadline outside 1..240 seconds")
        self.timeout = timeout
        self.stderr = log.open("w")
        self.process = subprocess.Popen(
            compose + ["run", "--rm", "-T", "--no-deps", "--name", name, "observer"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            bufsize=1,
        )

    def query(self, source):
        if "\n" in source:
            raise ValueError("one native expression per line")
        self.process.stdin.write(source + "\n")
        self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(self.timeout):
                raise TimeoutError(
                    f"native IPC process did not answer within {self.timeout} seconds"
                )
        line = self.process.stdout.readline()
        fields = line.rstrip("\n").split("\t")
        if len(fields) != 3 or fields[0] not in {"ok", "error"}:
            raise RuntimeError("invalid native IPC adapter reply: " + repr(line))
        return {
            "status": fields[0],
            "elapsed_ns": int(fields[1]),
            "body": bytes.fromhex(fields[2]).decode("utf-8"),
        }

    def close(self):
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        finally:
            self.stderr.close()
        if code:
            raise RuntimeError("native IPC adapter exited " + str(code))


def inspect_owned(container):
    value = json.loads(bench.capture(["docker", "inspect", container], text=True))[0]
    labels = value["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != "rayforce-duckdb-journal":
        raise RuntimeError("container is outside this journal project")
    return value


def capability():
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root = bench.RESULTS / "journal" / uuid.uuid4().hex
        state = root / "state"
        state.mkdir(parents=True)
        env = dict(
            os.environ,
            JOURNAL_STORAGE_PATH=str(state),
            LAB_UID=str(os.getuid()),
            LAB_GID=str(os.getgid()),
        )
        compose = [
            "docker",
            "compose",
            "-f",
            str(bench.ROOT / "docker-compose.journal.yml"),
        ]
        records, snapshots = [], []
        ingestion.save(
            root / "setup.json",
            {
                "scope": "Native synchronous IPC/journal capability, not live failure qualification or a benchmark",
                "docker_info": json.loads(
                    bench.capture(
                        ["docker", "info", "--format", "{{json .}}"], text=True
                    )
                ),
                "source_sha256": {
                    str(p.relative_to(bench.ROOT)): hashlib.sha256(
                        p.read_bytes()
                    ).hexdigest()
                    for p in [
                        Path(__file__),
                        bench.ROOT / "analytics-workload/cpp/ipc.cpp",
                        bench.ROOT / "docker-compose.journal.yml",
                        bench.ROOT / "docker/Dockerfile",
                    ]
                },
            },
        )
        ipc = None
        try:
            subprocess.run(
                compose + ["up", "-d", "rayforce"], env=env, check=True, timeout=60
            )
            server = subprocess.check_output(
                compose + ["ps", "-q", "rayforce"], env=env, text=True, timeout=30
            ).strip()
            initial = inspect_owned(server)
            ingestion.save(root / "initial-inspect.json", initial)
            if "RAYFORCE_NO_FSYNC=0" not in initial["Config"]["Env"]:
                raise RuntimeError("native fsync was not explicitly enabled")
            ipc = NativeIPC(
                compose,
                env,
                "rayforce-journal-probe-" + root.name[:8],
                root / "ipc.stderr",
            )

            def query(source, expected=None, retry_connect=False):
                deadline = time.monotonic() + 30
                while True:
                    record = dict(source=source, **ipc.query(source))
                    records.append(record)
                    ingestion.save(root / "requests.json", records)
                    if record["status"] == "ok":
                        break
                    if not retry_connect or time.monotonic() > deadline:
                        raise RuntimeError("native request failed: " + str(record))
                    time.sleep(0.2)
                if expected is not None and record["body"] != expected:
                    raise RuntimeError("native body mismatch: " + str(record))
                return record

            query("(+ 1 1)", "2", retry_connect=True)
            query("(get (.log.status) 'open)", "true")
            query("(get (.log.status) 'mode)", "sync")
            query("(set durable_probe 42)", "42")
            for phase in ("before-kill", "after-recovery"):
                snapshots.append(
                    {
                        "phase": phase,
                        "monotonic_ns": time.monotonic_ns(),
                        "inspection": inspect_owned(server),
                        "resource": bench.resource_sample(
                            bench.docker_socket(), server
                        ),
                    }
                )
                ingestion.save(root / "snapshots.json", snapshots)
                if phase == "before-kill":
                    subprocess.run(
                        ["docker", "kill", "--signal", "KILL", server],
                        check=True,
                        timeout=30,
                    )
                    stopped = inspect_owned(server)
                    ingestion.save(root / "killed-inspect.json", stopped)
                    if (
                        stopped["State"]["Running"]
                        or stopped["State"]["ExitCode"] != 137
                    ):
                        raise RuntimeError("old server was not proven killed")
                    subprocess.run(
                        compose + ["start", "rayforce"], env=env, check=True, timeout=30
                    )
                    # Only the read-only readiness probe reconnects/retries. Mutations never do.
                    query("(+ 1 1)", "2", retry_connect=True)
                    query("durable_probe", "42")
                    query("(get (.log.status) 'mode)", "sync")
            ingestion.save(
                root / "result.json",
                {
                    "passed": True,
                    "scope": "One scalar synchronous native journal capability gate after fenced SIGKILL; no live reader/writer or power-loss qualification",
                    "requests": records,
                    "snapshots": snapshots,
                    "state_files": {
                        str(p.relative_to(state)): p.stat().st_size
                        for p in state.rglob("*")
                        if p.is_file()
                    },
                },
            )
            print("Native synchronous journal capability passed:", root, flush=True)
        finally:
            try:
                if ipc is not None:
                    ipc.close()
            finally:
                try:
                    with (root / "server.log").open("w") as log:
                        subprocess.run(
                            compose + ["logs", "--no-color"],
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=30,
                            check=True,
                        )
                finally:
                    subprocess.run(compose + ["down"], env=env, check=True, timeout=60)


if __name__ == "__main__":
    capability()
