"""Fixed local namespaces; retained cases and HTTP qualification never share state."""

import os

OWNER = "iceberg-multi-system"
PROFILE = os.environ.get("LAB_PROFILE", "current")
if PROFILE not in {"historical", "current"}:
    raise RuntimeError("LAB_PROFILE must be historical or current")
PROJECT = OWNER + ("-current" if PROFILE == "current" else "")
RESULTS_SUBDIR = "results/current" if PROFILE == "current" else "results"
VARIANT = os.environ.get("LAB_VARIANT", "")
if VARIANT not in {"", "http"} or (VARIANT and PROFILE != "current"):
    raise RuntimeError("LAB_VARIANT must be empty or http with LAB_PROFILE=current")
if VARIANT:
    PROJECT += "-http"
    RESULTS_SUBDIR = "results/current-http"
DEFAULT_RESULTS = RESULTS_SUBDIR + "/manual"
CURRENT_FILES = (
    "deploy/compose/docker-compose.storage-current.yml",
    "deploy/compose/docker-compose.maintenance-current.yml",
    "deploy/compose/docker-compose.current.yml",
)
MAINTENANCE = (
    {"spark_version": "4.1.4", "iceberg_version": "1.12.0"}
    if PROFILE == "current"
    else {"spark_version": "4.0.1", "iceberg_version": "1.10.1"}
)

GO_IMAGE = (
    "iceberg-multi-current-runner-duckdb2:dev"
    if PROFILE == "current"
    else "iceberg-multi-runner-duckdb2:dev"
)


def validate_results(value, root):
    from pathlib import Path

    root = Path(root).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    scope = root / RESULTS_SUBDIR
    if not resolved.is_relative_to(scope.resolve()) or (
        PROFILE == "historical"
        and any(
            resolved.is_relative_to(root / name)
            for name in ("results/current", "results/current-http")
        )
    ):
        raise RuntimeError("RESULTS_DIR is outside the selected lab's namespace")
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise RuntimeError("symlinked result namespace is not accepted")
        if parent == root:
            break
    return resolved


if __name__ == "__main__":
    from pathlib import Path

    validate_results(
        os.environ.get("RESULTS_DIR", DEFAULT_RESULTS), Path(__file__).resolve().parents[1]
    )
