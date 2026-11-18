"""Stream ordered replies from the pinned native replica-targeted vespa-get CLI."""

import gzip

import ijson


def iter_copy(path, expected_count):
    count = 0
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as source:
        # This literal is native absence output outside JSON, never a document value.
        lines = (
            b"null\n" if line.strip() == b"Document not found." else line
            for line in source
        )
        events = ijson.parse(ijson.from_iter(lines), use_float=True)
        try:
            if next(events, None) != ("", "start_array", None):
                raise RuntimeError("native reply must be a JSON array")
            for index, document in enumerate(ijson.items(events, "item")):
                count += 1
                if index >= expected_count:
                    raise RuntimeError("native reply count exceeds requested IDs")
                if document is not None and (
                    not isinstance(document, dict)
                    or document.get("id") != f"id:catalog:product::{index}"
                    or document.get("fields", {}).get("pid") != index
                ):
                    raise RuntimeError(
                        "native reply does not match requested ID/position"
                    )
                yield document
        except ijson.JSONError as error:
            raise RuntimeError("malformed or incomplete native JSON reply") from error
    if count != expected_count:
        raise RuntimeError("native reply count does not match requested IDs")


def read_copy(path, expected_count):
    # Compatibility for the existing small metadata tests. Full vector audits stream.
    return list(iter_copy(path, expected_count))


def canonical_metadata(fields, expected):
    """Vespa omits empty strings; omission of any nonempty/numeric value is invalid."""
    allowed = (set(expected) - {"id"}) | {"pid", "embedding"}
    if set(fields) - allowed:
        raise RuntimeError("unexpected native fields")
    result = {"id": expected["id"]}
    for name, value in expected.items():
        if name == "id":
            continue
        if name not in fields:
            if value != "" or not isinstance(value, str):
                raise RuntimeError("missing native field: " + name)
            result[name] = ""
        else:
            if type(fields[name]) is not type(value):
                raise RuntimeError("wrong native field type: " + name)
            result[name] = fields[name]
    return result
