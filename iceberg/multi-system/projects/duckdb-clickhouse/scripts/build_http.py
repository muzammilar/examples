"""Build the HTTP image while preserving an existing pinned native runner."""

import json

import recovery_control as control


def main():
    if control.lab.PROFILE != "current":
        raise RuntimeError("HTTP image requires LAB_PROFILE=current")
    control.local_docker()
    present = control.call(
        ["docker", "image", "ls", "-q", "--filter", "reference=" + control.lab.GO_IMAGE]
    ).strip()
    if not present:
        print(control.call(control.COMPOSE + ["build", "runner"], timeout=600))
    (base,) = json.loads(
        control.call(["docker", "image", "inspect", control.lab.GO_IMAGE])
    )
    labels = base["Config"].get("Labels", {})
    if (
        labels.get("org.examples.owner") != control.OWNER
        or labels.get("org.examples.duckdb.version") != "2.0.1"
    ):
        raise RuntimeError("HTTP base must be the owned DuckDB 2.0.1 image")
    print(
        control.call(
            control.COMPOSE
            + ["-f", "deploy/compose/docker-compose.http.yml", "build", "runner"],
            timeout=240,
        )
    )


if __name__ == "__main__":
    main()
