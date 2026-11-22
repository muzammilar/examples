"""Inventory declared operational targets; never infer executed coverage from names."""

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
tracked = subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()
new = set(
    subprocess.check_output(
        [
            "git",
            "log",
            "--since=2026-10-16T00:00:00Z",
            "--diff-filter=A",
            "--format=",
            "--name-only",
            "--",
            "**/Makefile",
        ],
        cwd=root,
        text=True,
    ).splitlines()
)
excluded = {"datasets/esci/Makefile", "iceberg/benchmarks/Makefile"}
makefiles = [
    Path(name)
    for name in tracked
    if Path(name).name == "Makefile"
    and name in new
    and name not in excluded
    and "/dependencies/" not in name
]
reproducer = Path("greptimedb/wal-bootstrap-reproducer/Makefile")
if (root / reproducer).exists() and reproducer not in makefiles:
    makefiles.append(reproducer)
applications = {
    "go",
    "rust",
    "cpp",
    "python",
    "erlang",
    "js",
    "qemu",
    "boilerplate-for-multiple-languages",
    "datasets",
}
checks = {
    "suite": {"test-production"},
    "crash": {
        "crash",
        "crash-test",
        "test-crash",
        "failover",
        "chaos",
        "chaos-node",
        "test-failover",
    },
    "disk": {"disk-failure", "disk-replacement", "test-disk-failure"},
    "scale-out": {"scale-out"},
    "scale-in": {"scale-in"},
    "rolling": {"rolling-restart", "rolling-upgrade"},
    "partition": {"partition", "network-partition", "chaos-network"},
    "pressure": {"pressure", "chaos-oom", "chaos-cpu", "disk-full"},
    "restore": {"backup-restore", "restore-test", "test-restore"},
    "drain": {"drain", "test-drain", "chaos-drain"},
}
rows = []
for makefile in makefiles:
    folder = makefile.parent
    text = (root / makefile).read_text()
    targets = set()
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_ -]+):(?:\s|$)", line)
        if match:
            targets.update(match[1].split())
    if "dependencies" in folder.parts:
        shape = "support"
    elif folder.parts[0] in applications:
        shape = "application/fixture"
    elif "kubectl" in text or "kind create cluster" in text:
        shape = "Kubernetes"
    elif "docker compose" in text or "docker-compose" in text:
        shape = "Compose"
    else:
        shape = "other; review"
    declared = [name for name, aliases in checks.items() if targets & aliases]
    missing = [name for name in checks if name not in declared]
    note = "not runtime-audited"
    if shape == "Kubernetes" and "export KUBECONFIG" not in text:
        note += "; private kubeconfig export not found in Makefile"
    rows.append((folder.as_posix(), shape, declared, missing, note))

out = [
    "# Production-test audit",
    "",
    f"Inventory generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}: {len(rows) - 1} deployment examples first added since 2026-10-16, plus the current minimal WAL reproducer.",
    "",
    "Scope follows the user-requested last-month cohort. Older systems, including legacy ClickHouse examples, are excluded. Supporting Makefiles, ESCI fixtures and non-runnable Iceberg benchmark tooling are excluded.",
    "",
    "This is a source inventory, not a passing-test report. Target aliases can omit a",
    "test implemented elsewhere or match an incomplete controller. Missing targets",
    "require manual applicability review; they are not automatically unsupported.",
    "Live writers/readers, witness fsync, fault evidence, native repair, reconciliation",
    "and measured results must be checked in the actual code and execution logs.",
    "",
    "Generate the ignored target inventory with `python3 tools/audit-production-tests.py`. Run destructive tests",
    "serially on example-owned infrastructure with a private explicit context.",
    "",
    "| Example | Shape | Declared categories | No recognized target; review applicability | Evidence status |",
    "|---|---|---|---|---|",
]
for folder, shape, declared, missing, note in rows:
    out.append(
        f"| [{folder}]({folder}/) | {shape} | {', '.join(declared) or 'none'} | {', '.join(missing)} | {note} |"
    )
output = root / "tools/results/production-target-inventory.md"
output.parent.mkdir(exist_ok=True)
output.write_text("\n".join(out) + "\n")
print(f"Inventoried {len(rows)} Makefiles; no test execution inferred")
