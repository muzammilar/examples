"""Bootstrap native HA state without formatting surviving or acknowledged data."""

import json
from pathlib import Path
import time

import lab


def version(service, path):
    code = (
        "from pathlib import Path; import json; p=Path("
        + repr(path)
        + "); print(json.dumps(p.read_text() if p.exists() else None))"
    )
    return json.loads(
        lab.command(
            "run",
            "--rm",
            "--no-deps",
            "--user",
            "hadoop",
            service,
            "python3",
            "-c",
            code,
        ).stdout
    )


def native(service, *args):
    result = lab.command(
        "run",
        "--rm",
        "--no-deps",
        "--use-aliases",
        "--user",
        "hadoop",
        service,
        *args,
        timeout=180
    )
    (DIRECTORY / (service + "-" + str(time.time_ns()) + ".log")).write_text(
        result.stdout + result.stderr
    )
    return result


def main():
    global DIRECTORY
    DIRECTORY = lab.ROOT / "results" / ("bootstrap-" + str(time.time_ns()))
    DIRECTORY.mkdir(parents=True)
    first = version("nn1", "/data/name/current/VERSION")
    second = version("nn2", "/data/name/current/VERSION")
    (DIRECTORY / "before.json").write_text(
        json.dumps({"nn1": first, "nn2": second}, indent=2) + "\n"
    )
    index = lab.ROOT / "results/fixture.json"
    retired = None
    if first is None and index.exists():
        retired = json.loads(index.read_text())
        proofs = [
            json.loads(p.read_text())
            for p in (lab.ROOT / "results").glob("teardown-*/completed.json")
        ]
        if not any(
            p.get("infrastructure_removed")
            and p.get("fixture", {}).get("id") == retired["id"]
            for p in proofs
        ):
            raise RuntimeError(
                "Prior fixture exists without verified teardown; restore it, never silently format"
            )
    if first is None:
        if second is not None or any(
            version(node, "/data/data/current/VERSION") is not None
            for node in ("dn1", "dn2", "dn3")
        ):
            raise RuntimeError(
                "Surviving HA/data state exists; refusing NameNode format"
            )
        history = lab.command(
            "run",
            "--rm",
            "--no-deps",
            "tools",
            "python3",
            "-c",
            "from pathlib import Path; print(any(Path('/witness').glob('*/journal.jsonl')))",
        ).stdout.strip()
        if history != "False":
            raise RuntimeError(
                "Acknowledgement history exists; restore metadata instead of formatting"
            )
        native("nn1", "hdfs", "namenode", "-format", "-nonInteractive")
        if retired is not None:
            archive = lab.ROOT / "results/retired-fixtures"
            archive.mkdir(exist_ok=True)
            with (archive / (retired["id"] + ".json")).open("x") as output:
                output.write(index.read_text())
            index.unlink()

    marker = version("nn1", "/control/zookeeper-initialized")
    if marker is None:
        native("nn1", "hdfs", "zkfc", "-formatZK", "-nonInteractive")
        code = "from pathlib import Path; import os; p=Path('/control/zookeeper-initialized'); f=p.open('x'); f.write('lab\\n'); f.flush(); os.fsync(f.fileno()); f.close()"
        native("nn1", "python3", "-c", code)
    lab.command("up", "-d", "--wait", "nn1", "tools", "witness-control", timeout=180)
    lab.command("up", "-d", "zkfc1")
    if second is None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            observed = lab.states()
            if observed["nn1"].get("State") == "active":
                break
            time.sleep(1)
        else:
            raise TimeoutError(
                "Initial NN did not become active before standby bootstrap"
            )
        native("nn2", "hdfs", "namenode", "-bootstrapStandby", "-nonInteractive")
    lab.command("up", "-d", "--wait", "nn2", "zkfc2", "dn1", "dn2", "dn3", timeout=180)
    roles = lab.wait_roles()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        active = next(n for n, v in roles.items() if v["State"] == "active")
        code = (
            "import json,urllib.request; print(json.load(urllib.request.urlopen("
            + repr(
                "http://"
                + active
                + ":9870/jmx?qry=Hadoop:service=NameNode,name=FSNamesystemState"
            )
            + ',timeout=2))["beans"][0]["NumLiveDataNodes"])'
        )
        if lab.execute("tools", "python3", "-c", code).stdout.strip() == "3":
            break
        time.sleep(1)
    else:
        raise TimeoutError("Three DataNodes did not register")
    lab.execute("tools", "hdfs", "dfsadmin", "-safemode", "wait")
    lab.execute("tools", "hdfs", "dfs", "-mkdir", "-p", "/lab/objects", "/lab/staging")
    first = version("nn1", "/data/name/current/VERSION")
    second = version("nn2", "/data/name/current/VERSION")
    fields = lambda text: dict(
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.startswith("#")
    )
    a, b = fields(first), fields(second)
    for key in ("namespaceID", "clusterID", "blockpoolID"):
        if a[key] != b[key]:
            raise ValueError("NameNode namespace identity differs: " + key)
    (DIRECTORY / "completed.json").write_text(
        json.dumps({"roles": roles, "nn1": a, "nn2": b, "live_datanodes": 3}, indent=2)
        + "\n"
    )
    print(json.dumps({"bootstrap": "passed", "evidence": str(DIRECTORY)}))


if __name__ == "__main__":
    main()
