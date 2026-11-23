"""Refuse unsupported populated-cluster downgrades before Helm mutates it."""

import json
import os
import sys

from runtime import kubectl

if not kubectl(
    "get",
    "crd",
    "greptimedbclusters.greptime.io",
    "--ignore-not-found",
    "-o",
    "name",
    capture=True,
).strip():
    sys.exit(0)

existing = kubectl(
    "get", "greptimedbclusters", "--ignore-not-found", "-o", "json", capture=True
)
requested = os.environ.get("DB_VERSION", "v1.3.1")
for cluster in json.loads(existing)["items"]:
    if cluster["metadata"]["name"] != "telemetry":
        continue
    current = cluster["spec"]["version"]
    if current not in ("v1.3.0", "v1.3.1"):
        raise RuntimeError("unqualified existing database version: " + current)
    if current == "v1.3.1" and requested == "v1.3.0":
        raise RuntimeError("refusing database downgrade v1.3.1 -> v1.3.0")
