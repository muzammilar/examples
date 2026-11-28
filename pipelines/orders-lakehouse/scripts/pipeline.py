"""Native probes and bounded actors for this particular lakehouse."""

import json
import os
import subprocess
import time
import lab


def wait(probe, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (subprocess.SubprocessError, ValueError, RuntimeError) as error:
            last = str(error)
        time.sleep(2)
    raise RuntimeError("Probe timed out: " + str(last))


def control(path, mode):
    rows = lab.records(lab.actor(path, "lake-control", [mode], timeout=90))
    return next(r for r in rows if "actor_counters" not in r)


def rest(endpoint):
    raw = lab.compose(
        "exec",
        "-T",
        "jobmanager",
        "curl",
        "--fail",
        "--silent",
        "--max-time",
        "10",
        "http://jobmanager:8081/" + endpoint,
        timeout=20,
    )
    return json.loads(raw)


def job():
    jobs = rest("jobs/overview")["jobs"]
    selected = [
        j
        for j in jobs
        if j["name"] == "orders-lakehouse"
        and j["state"] not in ("FAILED", "CANCELED", "FINISHED", "SUSPENDED")
    ]
    if len(selected) != 1 or selected[0]["state"] != "RUNNING":
        return None
    return selected[0]


def metadata(path):
    return control(path, "metadata")["metadata_location"]


def summary(path, uri=None):
    uri = uri or metadata(path)
    rows = lab.records(lab.actor(path, "lake-oracle", ["summary", uri], timeout=120))
    return next(r for r in rows if "snapshots" in r)


def settled(path, phases, require_running=True):
    expected = len(phases) * int(os.environ.get("ORDER_COUNT", "1000000"))

    def probe():
        row = summary(path)
        actual = int(row["summary"].get("total-records", "0"))
        if actual > expected:
            raise ValueError("Unexpected extra records; inspect duplicate publication")
        return row if actual == expected and (not require_running or job()) else None

    return wait(probe, 240)


def verify(path, phases, frozen=None, replicas=False):
    snap = frozen or settled(path, phases)
    args = [
        "verify",
        snap["metadata"],
        "--phases",
        ",".join(phases),
        "--snapshot",
        str(snap["snapshot"]),
    ]
    if replicas:
        args.append("--replicas")
    row = next(
        r
        for r in lab.records(lab.actor(path, "lake-oracle", args, timeout=300))
        if "passed" in r
    )
    # The independent body oracle is the gate; Spark separately exercises its native reader.
    spark = lab.actor(path, "spark-job", ["verify", str(snap["snapshot"])], timeout=240)
    result = next(r for r in lab.records(spark) if "aggregates" in r)
    total = int(os.environ.get("ORDER_COUNT", "1000000"))
    customers = int(os.environ.get("CUSTOMER_COUNT", "10000"))
    cents = sum(100 + k % 1000 for k in range(1, total + 1))
    expected = [
        dict(
            phase=phase,
            rows=total,
            distinct_ids=total,
            cents=cents,
            customers=customers,
        )
        for phase in sorted(phases)
    ]
    if result["aggregates"] != expected:
        raise ValueError("Spark snapshot aggregate mismatch")
    return dict(snapshot=snap, oracle=row, spark=result)


class Background:
    def __init__(self, path, role, args):
        self.path, self.role = path, role
        self.name = lab.PROJECT + "-" + role + "-" + str(time.time_ns())
        self.logpath = path / (self.name + ".log")
        self.log = self.logpath.open("x")
        self.finished = False
        self.process = subprocess.Popen(
            [
                *lab.COMPOSE,
                "run",
                "--name",
                self.name,
                "--use-aliases",
                role,
                *map(str, args),
            ],
            cwd=lab.ROOT,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )

    def alive(self):
        if self.process.poll() is not None:
            raise RuntimeError("Actor exited early: " + self.name)
        return lab.owned(self.name, self.role)["State"]["Running"]

    def finish(self, stop=False):
        if self.finished:
            return self.logpath
        if stop and self.process.poll() is None:
            lab.owned(self.name, self.role)
            lab.output("docker", "kill", "--signal", "TERM", self.name)
        try:
            code = self.process.wait(timeout=660)
        except subprocess.TimeoutExpired:
            lab.owned(self.name, self.role)
            lab.output("docker", "stop", "--time", "20", self.name, timeout=45)
            raise
        finally:
            self.log.close()
        row = lab.owned(self.name, self.role)
        lab.save(self.path / (self.name + "-inspect.json"), row)
        if code or row["State"]["ExitCode"]:
            raise RuntimeError("Actor failed, retained: " + self.name)
        lab.output("docker", "rm", self.name)
        self.finished = True
        return self.logpath
