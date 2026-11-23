"""Verify preserved SDK-view exports; these share one physical catalog."""

import gzip
import hashlib
import json
from pathlib import Path


def audit_views(report, root, members, documents):
    copies = report["views"]
    scope = report["scope"]
    if scope != "views-of-one-shared-catalog" or set(copies) != set(members):
        raise ValueError("invalid or incomplete catalog-view proof")
    acknowledgements = {}
    for witness in sorted((root / "cases").glob("*/writer.jsonl")):
        for line in witness.read_text().splitlines():
            event = json.loads(line)
            if event["stage"] == "ack":
                for row in event["rows"]:
                    acknowledgements[row["id"]] = max(
                        acknowledgements.get(row["id"], 0), row["version"]
                    )
    results = {}
    for host, info in copies.items():
        if info["rows"] != documents or info["vector_coordinates"] != documents * 384:
            raise ValueError("native row/vector coverage differs from input manifest")
        name = info["raw"]
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("unsafe native export path")
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("native export escaped evidence root")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != info["raw_sha256"]:
                raise ValueError("native export bytes changed")
        logical = hashlib.sha256()
        count = 0
        visible_nonack = 0
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("id") != str(count):
                    raise ValueError("missing, duplicate or reordered exported product")
                floor = acknowledgements.get(row["id"], 0)
                if type(row.get("version")) is not int or row["version"] < floor:
                    raise ValueError(
                        "export regressed below the current acknowledged version"
                    )
                visible_nonack += row["version"] > floor
                vector_hash = row.get("vector_sha256")
                if (
                    not isinstance(vector_hash, str)
                    or len(vector_hash) != 64
                    or any(c not in "0123456789abcdef" for c in vector_hash)
                ):
                    raise ValueError("invalid vector fingerprint")
                logical.update(
                    (
                        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode()
                )
                count += 1
        if count != documents:
            raise ValueError("truncated native row export")
        results[host] = {
            "rows": count,
            "logical_sha256": logical.hexdigest(),
            "visible_latest_nonack_versions": visible_nonack,
        }
    if (
        scope == "views-of-one-shared-catalog"
        and len({v["logical_sha256"] for v in results.values()}) != 1
    ):
        raise ValueError("catalog views differ at quiescence")
    return {
        "scope": scope,
        "views": results,
        "all_required_views_identical": scope == "views-of-one-shared-catalog",
    }
