"""Serial example-owned production cases; create the restore/repair source here."""

import argparse
import subprocess
import sys

import experiment as bench
from live_journal import run


def command(*arguments):
    subprocess.run(
        [sys.executable, *arguments], cwd=bench.ROOT, check=True, timeout=600
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("all", "recovery"), default="all")
    args = parser.parse_args()
    failed_diagnostics = []
    try:
        command("journal.py")
        command("filesystem_lock_check.py", "volume")
        subprocess.run(
            ["make", "test-journal-writer-lock"],
            cwd=bench.ROOT,
            check=True,
            timeout=600,
        )
        source = run()
        command("journal_restore.py", str(source))
        command("journal_tail.py", str(source))
        if args.profile == "all":
            command("journal_header.py", str(source))
            # These independent offline fault models do not consume each other's
            # outputs. Preserve both outcomes without qualifying either failure.
            for arguments in (("journal_corruption.py", str(source)), ("aof.py",)):
                try:
                    command(*arguments)
                except subprocess.CalledProcessError:
                    failed_diagnostics.append(arguments[0])
    finally:
        for resource, flags in (
            ("ps", ["-aq"]),
            ("network", ["ls", "-q"]),
            ("volume", ["ls", "-q"]),
        ):
            result = bench.capture(
                [
                    "docker",
                    resource,
                    *flags,
                    "--filter",
                    "label=com.docker.compose.project=rayforce-duckdb-journal",
                ],
                text=True,
            ).strip()
            if result:
                raise RuntimeError("journal-owned resources remain: " + resource)
    if failed_diagnostics:
        print(
            "FAILED production diagnostics:", ", ".join(failed_diagnostics), flush=True
        )
        raise SystemExit(1)
    print(
        "Serial production profile passed:", args.profile, "source:", source, flush=True
    )
