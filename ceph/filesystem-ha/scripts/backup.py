"""Freeze an ACK cut, export bodies, and restore only into a different fresh FSID."""

import hashlib
import json
import os
from pathlib import Path
import re
import resource
import sys
import tarfile
import time
import uuid
import lab
import test as checks


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_archive(path, run, expected_journal):
    with tarfile.open(path, "r:") as archive:
        members = archive.getmembers()
        names = [m.name.rstrip("/") for m in members]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive member")
        if any(
            not (m.isfile() or (m.isdir() and m.name.rstrip("/") == "bodies"))
            or not re.fullmatch(r"journal\.jsonl|bodies/?|bodies/[0-9]+", m.name)
            for m in members
        ):
            raise ValueError("Unexpected archive entry or link")
        witness = archive.extractfile("journal.jsonl").read()
        if witness != expected_journal:
            raise RuntimeError("Backup witness differs from external ACK cut")
        records = [json.loads(line) for line in witness.splitlines()]
        acks = [r for r in records if r["event"] == "ack"]
        if set(names) - {"bodies", "journal.jsonl"} != {
            "bodies/" + str(r["sequence"]) for r in acks
        }:
            raise RuntimeError("Backup bodies do not cover exactly the ACK set")
        for row in acks:
            data = archive.extractfile("bodies/" + str(row["sequence"])).read()
            expected = hashlib.shake_256(
                f"42193:{run}:{row['sequence']}".encode()
            ).digest(row["bytes"])
            if data != expected or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise RuntimeError("Actual backup bytes fail independent oracle")
    return {"acks": len(acks), "bytes": sum(row["bytes"] for row in acks)}


def main():
    mode = sys.argv[1]
    directory = lab.ROOT / "results" / ("backup-" + mode + "-" + str(time.time_ns()))
    directory.mkdir()
    started = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    checks.save(
        directory / "resources-before.json",
        json.loads(lab.native("resources.py").stdout),
    )
    if mode == "create":
        fixture = checks.fixture()
        audit = checks.audit(directory)
        backup = uuid.uuid4().hex
        folder = lab.ROOT / "results/backups" / backup
        folder.mkdir(parents=True)
        entries = []
        for case_file in sorted((lab.ROOT / "results/cases").glob("*/case.json")):
            case = json.loads(case_file.read_text())
            if case["fixture"] != fixture["id"] or case.get(
                "blocked_before_publication"
            ):
                continue
            witness = checks.export(case["node"], case["run"])
            history = checks.history(witness, case["run"])
            result = lab.native(
                "backup.py",
                "create",
                case["run"],
                backup,
                str(witness.relative_to(lab.ROOT / "results")),
                timeout=900,
            )
            (directory / (case["run"] + ".log")).write_text(
                result.stdout + result.stderr
            )
            row = next(
                r
                for r in checks.events(result.stdout)
                if r.get("event") == "backup_archive"
            )
            path = lab.ROOT / "results" / row["path"]
            bodies = validate_archive(path, case["run"], witness.read_bytes())
            again = checks.export(case["node"], case["run"])
            if again.read_bytes() != witness.read_bytes():
                raise RuntimeError("ACK cut changed while backing up")
            entries.append(
                row
                | bodies
                | {
                    "run": case["run"],
                    "journal_sha256": hashlib.sha256(witness.read_bytes()).hexdigest(),
                    "ambiguous_or_incomplete": history["ambiguous_or_incomplete"],
                }
            )
        if sum(row["acks"] for row in entries) != audit["total_acks"]:
            raise RuntimeError("Backup is missing an acknowledged case")
        manifest = {
            "backup": backup,
            "source_fixture": fixture,
            "entries": entries,
            "total_acks": audit["total_acks"],
            "total_bytes": audit["acknowledged_bytes"],
            "quiescent_cut": True,
            "file_level_only": True,
        }
        target = folder / "manifest.json"
        with target.open("x") as stream:
            stream.write(json.dumps(manifest, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        status = {"passed": True, "manifest": str(target.relative_to(lab.ROOT))}
    elif mode == "restore":
        path = Path(os.environ["BACKUP_MANIFEST"]).resolve()
        path.relative_to((lab.ROOT / "results/backups").resolve())
        manifest = json.loads(path.read_text())
        if path.parent.name != manifest["backup"] or path.name != "manifest.json":
            raise RuntimeError("Manifest path identity differs")
        if (lab.ROOT / "results/fixture.json").exists():
            raise RuntimeError("Restore requires a fresh lab without a data fixture")
        fsid = checks.ssh(0, "ceph fsid").stdout.strip()
        source = manifest["source_fixture"]
        if fsid == source["fsid"]:
            raise RuntimeError("Independent restore needs a different cluster FSID")
        fixture = source | {
            "id": uuid.uuid4().hex,
            "fsid": fsid,
            "stage": "restoring",
            "restored_from": manifest["backup"],
        }
        checks.save(lab.ROOT / "results/fixture.json", fixture)
        for row in manifest["entries"]:
            archive = lab.ROOT / "results" / row["path"]
            archive.resolve().relative_to(path.parent)
            if digest_file(archive) != row["sha256"]:
                raise RuntimeError("Archive changed since backup")
            with tarfile.open(archive, "r:") as stream:
                witness = stream.extractfile("journal.jsonl").read()
            if hashlib.sha256(witness).hexdigest() != row["journal_sha256"]:
                raise RuntimeError("Witness differs from manifest")
            if validate_archive(archive, row["run"], witness) != {
                "acks": row["acks"],
                "bytes": row["bytes"],
            }:
                raise RuntimeError("Archive counts differ")
            case = lab.ROOT / "results/cases" / (fixture["id"] + "-" + row["run"])
            case.mkdir()
            checks.save(
                case / "case.json",
                {
                    "fixture": fixture["id"],
                    "run": row["run"],
                    "node": 1,
                    "mode": "independent-restore",
                },
            )
            result = lab.native(
                "backup.py",
                "restore",
                row["run"],
                manifest["backup"],
                row["path"],
                timeout=900,
            )
            (directory / (row["run"] + ".log")).write_text(
                result.stdout + result.stderr
            )
        result = checks.audit(directory)
        if (
            result["total_acks"] != manifest["total_acks"]
            or result["acknowledged_bytes"] != manifest["total_bytes"]
        ):
            raise RuntimeError("Restored ACK cut is incomplete")
        fixture["stage"] = "ready"
        checks.save(lab.ROOT / "results/fixture.json", fixture)
        status = {
            "passed": True,
            "source_fsid": source["fsid"],
            "destination_fsid": fsid,
            "acknowledged_loss": 0,
            "regenerated": False,
            **result,
        }
    else:
        raise ValueError(mode)
    checks.save(
        directory / "resources-after.json",
        json.loads(lab.native("resources.py").stdout),
    )
    usage = resource.getrusage(resource.RUSAGE_SELF)
    checks.save(
        directory / "duration.json",
        {
            "elapsed_s": time.monotonic() - started,
            "host_user_cpu_s": usage.ru_utime - usage_before.ru_utime,
            "host_system_cpu_s": usage.ru_stime - usage_before.ru_stime,
            "host_process_peak_rss_bytes": usage.ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "host_io_bytes": None,
            "scope": "Controller self only; excludes Docker daemons and guest applications",
        },
    )
    checks.save(directory / "status.json", status)
    if mode == "create":
        print(target)


if __name__ == "__main__":
    main()
