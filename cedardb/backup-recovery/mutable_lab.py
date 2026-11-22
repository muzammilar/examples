"""Example-owned transactional mutation cases; failed schemas/histories are retained."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
import zlib

import control
import mutable_oracle
from resources import Recorder, summarize


class MutableCase:
    def __init__(self, backend, pairs=8):
        if backend not in control.BACKENDS:
            raise ValueError("unknown mutable backend")
        lab = json.loads((control.RESULTS / "lab.json").read_text())
        if lab["phase"] != "ready":
            raise RuntimeError("run make docker-up and make up first")
        self.backend, self.identity = backend, uuid.uuid4().hex
        self.rows, self.pairs = lab["rows"], pairs
        if not 1 <= pairs < self.rows // 200 * 100:
            raise ValueError(
                "mutable pair window must leave the historical pair intact"
            )
        self.root = control.RESULTS / "mutable" / self.identity
        self.root.mkdir(parents=True)
        self.schema = '"mutable_' + self.identity + '"'
        self.owner = "orders_owner" if backend == "cedar" else "orders_writer"
        self.env = {
            "CEDAR_LAB_DB_HOST": backend,
            "CEDAR_LAB_ENGINE": backend,
            "CEDAR_LAB_USER": self.owner,
            "CEDAR_LAB_PASSWORD": "Orders-Lab-1",
            "CEDAR_LAB_READ_ONLY": "0",
            "MUTABLE_RUN_ID": self.identity,
            "MUTABLE_PAIRS": str(pairs),
            "ROWS": str(self.rows),
        }
        self.actors, self.starts, self.cleaned, self.phases = {}, {}, set(), []
        self.config = {
            "created_ns": time.time_ns(),
            "backend": backend,
            "identity": self.identity,
            "generation": lab["generation"],
            "rows": self.rows,
            "pairs": pairs,
            "writer_rate": int(control.ENV.get("RATE", "50")),
            "reader_rate": int(control.ENV.get("MUTABLE_READ_RATE", "20")),
            "backend_contract": json.loads(
                (control.ROOT / "mutable-contract.json").read_text()
            ),
            "contract": "Synchronous repeatable-read transactions. Each paired UPDATE or DELETE/reinsert and its native receipt commit atomically. External intent/ACK journal; no mutation retries. Whole group totals stay fixed while actual queried bodies change.",
            "source_sha256": {
                str(p.relative_to(control.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    control.ROOT / "mutable_oracle.py",
                    control.ROOT / "mutable-contract.json",
                    control.ROOT / "control.py",
                    control.ROOT / "resources.py",
                    control.ROOT / "docker-compose.yml",
                    *sorted((control.ROOT / "orders-workload").glob("*.go")),
                    *[
                        control.ROOT / "orders-workload" / name
                        for name in ("go.mod", "go.sum", "Dockerfile")
                    ],
                ]
            },
        }
        control.save(self.root / "config.json", self.config)

    def command(self, mode, *args, env=None):
        try:
            raw = control.compose(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "orders-workload",
                mode,
                *args,
                env=self.env | (env or {}),
                timeout=300,
            )
            return [json.loads(line) for line in raw.splitlines()]
        except subprocess.CalledProcessError as error:
            control.save(
                self.root / (mode + "-failed.json"),
                {
                    "returncode": error.returncode,
                    "stdout": error.stdout,
                    "stderr": error.stderr,
                },
            )
            raise

    def seed(self):
        control.app(
            self.backend,
            "sql",
            "CREATE SCHEMA " + self.schema + " AUTHORIZATION " + self.owner,
        )
        result = self.command("mutable-seed")
        control.save(self.root / "seed.json", result)
        control.save(
            self.root / "negative-controls.json", self.command("mutable-controls")
        )

    def snapshot(self):
        snapshot = control.native_snapshot(self.backend)
        snapshot["base_table_compression"] = snapshot.pop("native_compression")
        if self.backend == "cedar":
            sql = (
                "SELECT v.attributename,v.encoding,v.generalcompression,v.uncompressedsize,v.encodedsize,v.compressedsize FROM cedardb_compression_info v JOIN pg_class c ON c.oid=v.oid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname='mutable_orders' AND n.nspname='mutable_"
                + self.identity
                + "'"
            )
            scope = "Native cold-column statistics for this case's mutable_orders; empty rows do not establish no compression"
        else:
            sql = (
                "SELECT coalesce(pg_column_compression(payload),'none'),count(*),sum(pg_column_size(payload)) FROM "
                + self.schema
                + ".mutable_orders GROUP BY pg_column_compression(payload)"
            )
            scope = "Actual mutable payload storage/compression; tuple, index, receipt, WAL and base fixture bytes are separate"
        try:
            snapshot["native_compression"] = {
                "available": True,
                "query": sql,
                "scope": scope,
                "records": control.app(self.backend, "sql", sql),
            }
        except subprocess.CalledProcessError as error:
            snapshot["native_compression"] = {
                "available": False,
                "query": sql,
                "scope": scope,
                "stderr": error.stderr,
                "stdout": error.stdout,
            }
        return snapshot

    def path(self, name):
        return "/results/" + str((self.root / name).relative_to(control.RESULTS))

    def start(self):
        for role in ("writer", "reader"):
            raw = control.compose(
                "run",
                "--detach",
                "--no-deps",
                "--name",
                "cedar-mutable-" + self.identity[:12] + "-" + role,
                "--label",
                "org.examples.mutable-case=" + self.identity,
                "orders-workload",
                "mutable-" + role,
                self.path(role + ".jsonl"),
                env=self.env,
            )
            info = control.inspect(raw.strip())
            if (
                info["Config"]["Labels"].get("org.examples.mutable-case")
                != self.identity
            ):
                raise RuntimeError("mutable actor case ownership differs")
            if (
                info["HostConfig"]["NanoCpus"] != 10**9
                or info["HostConfig"]["Memory"] != 512 * 1024**2
                or info["HostConfig"]["MemorySwap"] != 512 * 1024**2
            ):
                raise RuntimeError("mutable actor resource budget differs")
            self.actors[role] = info["Id"]
            self.starts[role] = info["State"]["StartedAt"]
            control.save(self.root / (role + "-started.json"), info)

    def events(self, role):
        return control.journal(self.root / (role + ".jsonl"), active=True)

    def assert_running(self):
        for role, identity in self.actors.items():
            info = control.inspect(identity)
            if not info["State"]["Running"]:
                raise RuntimeError("mutable actor stopped: " + role)
            if info["State"]["StartedAt"] != self.starts[role]:
                raise RuntimeError("independent mutable actor restarted")

    def progress(self, before=None):
        before = before or {role: 0 for role in self.actors}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.assert_running()
            counts = {
                role: sum(e["kind"] == "ok" for e in self.events(role))
                for role in self.actors
            }
            if all(counts[role] >= before[role] + 5 for role in counts):
                return counts
            time.sleep(0.1)
        raise TimeoutError("mutable reader/writer did not establish progress")

    def hold(self, recorder, label, seconds):
        recorder.phase = label
        phase = {"name": label, "started_ns": time.time_ns()}
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.assert_running()
            time.sleep(0.1)
        phase["ended_ns"] = time.time_ns()
        self.phases.append(phase)
        control.save(self.root / "phases.json", self.phases)

    def close(self):
        errors = []
        for role, identity in self.actors.items():
            if role in self.cleaned:
                continue
            info = control.inspect(identity)
            if info["State"]["Running"]:
                control.run(["docker", "stop", "--timeout", "10", identity])
            info = control.inspect(identity)
            control.save(self.root / (role + "-stopped.json"), info)
            logs = subprocess.run(
                ["docker", "logs", identity],
                cwd=control.ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=30,
            ).stdout
            (self.root / (role + ".log")).write_text(logs)
            if info["State"]["Running"] or info["State"]["ExitCode"] != 0:
                errors.append(role + " did not shut down cleanly")
                continue
            control.run(["docker", "rm", identity])
            self.cleaned.add(role)
        if errors:
            raise RuntimeError("; ".join(errors))

    def verify(self, concurrent=True):
        if not concurrent:
            if self.actors:
                raise RuntimeError("cannot declare an active workload absent")
            with (self.root / "writer.jsonl").open("x") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        for mode, name in (
            ("mutable-export", "rows.csv"),
            ("mutable-receipts", "receipts.csv"),
        ):
            control.save(
                self.root / (mode + ".json"), self.command(mode, self.path(name))
            )
        proof = mutable_oracle.reconcile(
            self.root / "rows.csv",
            self.root / "receipts.csv",
            self.root / "writer.jsonl",
            self.rows,
            self.pairs,
            self.root / "reader.jsonl" if concurrent else None,
        )
        compression = []
        for name in ("rows.csv", "receipts.csv"):
            codec, output, size = zlib.compressobj(1, zlib.DEFLATED, 31), 0, 0
            started, cpu = time.monotonic_ns(), time.process_time_ns()
            with (self.root / name).open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    output += len(codec.compress(block))
            output += len(codec.flush())
            compression.append(
                {
                    "file": name,
                    "codec": "gzip",
                    "level": 1,
                    "source_bytes": size,
                    "archive_bytes": output,
                    "ratio": size / output,
                    "wall_ns": time.monotonic_ns() - started,
                    "cpu_ns": time.process_time_ns() - cpu,
                    "scope": "Off-path CSV compressibility, not native engine compression",
                }
            )
        proof["compression"] = compression
        control.save(self.root / "verification.json", proof)
        return proof

    def drop_verified(self):
        if not (self.root / "verification.json").is_file() or any(
            role not in self.cleaned for role in self.actors
        ):
            raise RuntimeError("cannot drop an unverified or active mutable schema")
        control.app(self.backend, "sql", "DROP SCHEMA " + self.schema + " CASCADE")
        control.save(
            self.root / "cleanup.json", {"schema_dropped_after_verification": True}
        )


def run_case(backend, crash=False):
    case = MutableCase(backend)
    context = json.loads(control.run(["docker", "context", "inspect"]))[0]
    endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
    targets = [(backend, control.container(backend)["Id"])]
    native_before = control.native_snapshot(backend)
    passed = False
    try:
        with Recorder(
            case.root / "resources.jsonl", endpoint, targets, control.sample_disk
        ) as recorder:
            case.seed()
            case.start()
            recorder.targets.extend(case.actors.items())
            counts = case.progress()
            case.hold(recorder, "baseline", 5)
            if crash:
                counts = {
                    role: sum(e["kind"] == "ok" for e in case.events(role))
                    for role in case.actors
                }
                before = control.container(backend)
                other = control.container(
                    next(name for name in control.BACKENDS if name != backend)
                )
                control.run(["docker", "kill", "--signal", "KILL", before["Id"]])
                dead = control.inspect(before["Id"])
                control.save(case.root / "killed.json", dead)
                if dead["State"]["Running"] or dead["State"]["ExitCode"] != 137:
                    raise RuntimeError("mutable database crash not proved")
                case.hold(recorder, "outage", 5)
                recorder.phase = "native-recovery"
                control.compose("up", "--detach", "--wait", backend)
                case.progress(counts)
                now = control.container(backend)
                if now["State"]["StartedAt"] == before["State"]["StartedAt"]:
                    raise RuntimeError("mutable database generation did not change")
                if (
                    control.container(
                        other["Config"]["Labels"]["com.docker.compose.service"]
                    )["State"]["StartedAt"]
                    != other["State"]["StartedAt"]
                ):
                    raise RuntimeError("unrelated surviving database restarted")
                case.hold(recorder, "recovered", 10)
            else:
                recorder.phase = "analytics-under-mutations"
                control.save(
                    case.root / "analytics.json",
                    case.command(
                        "mutable-analytics",
                        env={"ANALYTICS_WARMUP_S": "1", "ANALYTICS_DURATION_S": "5"},
                    ),
                )
            case.close()
            recorder.phase = "reconciliation"
            proof = case.verify()
            native_after = case.snapshot()
        phases = {
            phase["name"]: {
                role: control.phase_summary(
                    [
                        e
                        for e in case.events(role)
                        if e.get("ended_ns", 0) <= phase["ended_ns"]
                    ],
                    phase["started_ns"],
                    phase["ended_ns"],
                )
                for role in case.actors
            }
            for phase in case.phases
        }
        for name in ("baseline", "recovered") if crash else ("baseline",):
            if any(phases[name][role]["ok"]["count"] < 5 for role in case.actors):
                raise RuntimeError("no independent healthy mutable progress")
            if any(
                phases[name][role][kind]["count"]
                for role in case.actors
                for kind in ("fail", "ambiguous", "mismatch")
            ):
                raise RuntimeError(
                    "unexpected request errors in a healthy mutable phase"
                )
        if crash and any(
            not sum(
                phases["outage"][role][kind]["count"] for kind in ("fail", "ambiguous")
            )
            for role in case.actors
        ):
            raise RuntimeError("both independent actors must observe the outage")
        result = {
            "passed": False,
            "scenario_passed": True,
            "backend": backend,
            "crash": crash,
            "proof": proof,
            "phases": phases,
            "native_before": native_before,
            "native_after": native_after,
            "resources": summarize(case.root / "resources.jsonl"),
            "scope": "Functional paired UPDATE/delete-reinsert recovery, not performance qualification",
        }
        control.save(case.root / "result.json", result)
        passed = True
    except Exception as error:
        control.save(
            case.root / "failure.json", {"passed": False, "error": repr(error)}
        )
        raise
    finally:
        case.close()
        if passed:
            case.drop_verified()
    result["passed"] = True
    control.save(case.root / "result.json", result)
    print("Mutable SQL case passed:", backend, crash, case.root, flush=True)
    return case.root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    with (control.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for backend in control.BACKENDS:
            run_case(backend, args.crash)
