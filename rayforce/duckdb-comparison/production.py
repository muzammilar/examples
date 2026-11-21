"""Serial example-owned production cases; create the restore/repair source here."""

import subprocess
import sys

import experiment as bench
from live_journal import run


def command(*arguments):
    subprocess.run(
        [sys.executable, *arguments], cwd=bench.ROOT, check=True, timeout=600
    )


if __name__ == "__main__":
    command("journal.py")
    command("filesystem_lock_check.py", "volume")
    subprocess.run(
        ["make", "test-journal-writer-lock"], cwd=bench.ROOT, check=True, timeout=600
    )
    source = run()
    command("journal_restore.py", str(source))
    command("journal_tail.py", str(source))
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
            raise RuntimeError(
                "journal-owned resources remain after production cases: " + resource
            )
    print("Serial production cases passed; source:", source, flush=True)
