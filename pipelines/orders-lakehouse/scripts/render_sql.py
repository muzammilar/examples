"""Render native Flink catalog settings from the same tracked properties as Spark."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render():
    properties = json.loads((ROOT / "deploy/catalog.json").read_text())
    if (
        properties["uri"] != "jdbc:postgresql://catalog-db:5432/lake?sslmode=disable"
        or properties["s3.endpoint"] != "http://s3:8333"
        or properties["warehouse"] != "s3://warehouse/"
    ):
        raise ValueError("Unexpected endpoint; cloud fallback forbidden")
    options = {"type": "iceberg", **properties, "jdbc.ApplicationName": "lake-flink"}
    catalog = (
        "CREATE CATALOG lake WITH (\n"
        + ",\n".join(
            " '" + key + "'='" + value.replace("'", "''") + "'"
            for key, value in options.items()
        )
        + "\n);"
    )
    template = (ROOT / "deploy/flink/pipeline.sql.in").read_text()
    if template.count("@@CATALOG@@") != 1:
        raise ValueError("Expected one catalog insertion point")
    (ROOT / ".build").mkdir(exist_ok=True)
    path = ROOT / ".build/pipeline.sql"
    path.write_text(template.replace("@@CATALOG@@", catalog))
    return path


if __name__ == "__main__":
    print(render())
