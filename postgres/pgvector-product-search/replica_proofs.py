"""Audit preserved native exports for exact quiescent cross-copy convergence."""

import argparse
import os
import gzip
import hashlib
import json
from pathlib import Path


def audit_copies(report, root, members, documents):
    copies = report["copies"]
    scope = report["scope"]
    if scope == "all-native-copies":
        if set(copies) != set(members):
            raise ValueError("full proof omitted or added a native member")
    elif scope == "rebuilt-member-live-snapshot":
        if len(copies) != 1 or not set(copies).issubset(members):
            raise ValueError("invalid rebuilt-copy scope")
    else:
        raise ValueError("unknown native proof scope")
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
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("id") != str(count):
                    raise ValueError("missing, duplicate or reordered exported product")
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
        results[host] = {"rows": count, "logical_sha256": logical.hexdigest()}
    if (
        scope == "all-native-copies"
        and len({v["logical_sha256"] for v in results.values()}) != 1
    ):
        raise ValueError("native copies differ after the same WAL fence")
    return {
        "scope": scope,
        "copies": results,
        "all_required_copies_identical": scope == "all-native-copies",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--mode",
        choices=["single", "cluster"],
        default=os.environ.get("MODE", "cluster"),
    )
    args = parser.parse_args()
    root = args.results.resolve()
    corpus = Path(__file__).resolve().parent / "results/corpus/prepared/manifest.json"
    documents = json.loads(corpus.read_text())["parameters"]["documents"]
    members = ["single"] if args.mode == "single" else ["pg0", "pg1", "pg2"]
    results = {}
    for path in sorted(root.glob("verification-*.json")):
        results[path.name] = audit_copies(
            json.loads(path.read_text()), root, members, documents
        )
    if not results:
        raise RuntimeError("no native proof reports to audit")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
