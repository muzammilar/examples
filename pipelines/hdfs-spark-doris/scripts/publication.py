"""Validate the immutable publication identity and exact file inventory."""

from pathlib import PurePosixPath
import re


def check_manifest(manifest, actual_files, batch, rows, customers):
    if (
        manifest.get("batch"),
        manifest.get("rows"),
        manifest.get("customers"),
        manifest.get("recipe"),
    ) != (batch, rows, customers, "orders-v1-one-based"):
        raise ValueError("Manifest identity/recipe/count mismatch")
    listed = {}
    for item in manifest["files"]:
        name = item["path"].lstrip("/")
        path = PurePosixPath(name)
        if (
            ".." in path.parts
            or path.parts[0] not in ("orders", "regions")
            or path.suffix != ".parquet"
            or not re.fullmatch(
                r"(?:orders/customer_shard=[012]|regions)/part-[^/]+\.parquet", name
            )
            or name in listed
            or item["bytes"] <= 0
            or not item["checksum"]
        ):
            raise ValueError("Invalid or duplicated manifest entry")
        listed[name] = item["bytes"]
    if not listed or listed != actual_files:
        raise ValueError("Manifest inventory mismatch")
    return listed
