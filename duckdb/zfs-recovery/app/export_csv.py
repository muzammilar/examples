"""Native CSV export inside a caller-owned fresh directory; preserve earlier files."""

import os
import re


def copy_csv(connection, table, path):
    if not re.fullmatch(r"live_[0-9a-f]{32}|events", table):
        raise ValueError("unexpected example-owned table")
    if path.exists():
        raise FileExistsError(path)
    destination = str(path).replace("'", "''")
    connection.execute(
        f"COPY (SELECT * FROM {table} ORDER BY event_id) TO '{destination}' "
        "(FORMAT CSV, HEADER TRUE, NEW_LINE E'\\r\\n')"
    )
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
