"""Strictly decode the pinned native vespa-get CLI's ordered replies."""

import json


def read_copy(path, expected_count):
    # DocumentRetriever prints this literal (outside JSON) for an absent replica.
    # Only replace the entire protocol line, never text inside a product field.
    with path.open(encoding="utf-8") as stream:
        payload = "".join(
            "null\n" if line.strip() == "Document not found." else line
            for line in stream
        )
    documents = json.loads(payload)
    if not isinstance(documents, list) or len(documents) != expected_count:
        raise RuntimeError("native reply count does not match requested IDs")
    for i, document in enumerate(documents):
        if document is not None and (
            document["id"] != f"id:catalog:product::{i}"
            or document["fields"]["pid"] != i
        ):
            raise RuntimeError("native reply does not match requested ID/position")
    return documents
