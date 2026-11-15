"""Local serving wrapper: one SDK writer, independent shared-volume readers."""

from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import traceback

import lancedb
from lancedb.index import BTree, FTS, IvfFlat
import pyarrow as pa
from workload import Fixture, META

fixture = Fixture()
db = lancedb.connect("/data/catalog", read_consistency_interval=timedelta(0))
readonly = os.environ.get("READ_ONLY") == "1"
write_lock = threading.Lock()


def arrow(rows):
    ids = [int(row["id"]) for row in rows]
    arrays = {key: pa.array([row[key] for row in rows]) for key in META}
    arrays["embedding"] = pa.FixedSizeListArray.from_arrays(
        pa.array(fixture.vectors[ids].ravel(), type=pa.float32()), 384
    )
    arrays["text"] = pa.array(
        [row["name"] + " " + row["brand"] + " " + row["description"] for row in rows]
    )
    return pa.table(arrays)


def table():
    return db.open_table("products")


def operate(action, body):
    if action in ("load", "update", "strength", "maintenance") and readonly:
        raise PermissionError("reader is read-only")
    if action == "load":
        start = time.perf_counter()
        t = db.create_table("products", data=arrow(fixture.products), mode="overwrite")
        inserted = time.perf_counter() - start
        t.create_index(
            "embedding", config=IvfFlat(distance_type="cosine", num_partitions=256)
        )
        t.create_index("tenant", config=BTree())
        t.create_index("text", config=FTS(stem=True, remove_stop_words=True))
        return {
            "insert_seconds": inserted,
            "index_seconds": time.perf_counter() - start - inserted,
            "version": t.version,
        }
    if action == "update":
        table().merge_insert(
            "id"
        ).when_matched_update_all().when_not_matched_insert_all().execute(
            arrow(body["rows"])
        )
        return {"updated": len(body["rows"])}
    if action == "search":
        t = table()
        mode = body["mode"]
        if mode == "lexical":
            q = t.search(body["text"], query_type="fts", fts_columns="text")
        elif mode == "hybrid":
            q = (
                t.search(
                    query_type="hybrid",
                    vector_column_name="embedding",
                    fts_columns="text",
                )
                .vector(body["vector"])
                .text(body["text"])
            )
        else:
            q = t.search(body["vector"], vector_column_name="embedding")
        if mode != "lexical":
            q = q.distance_type("cosine").nprobes(
                256
                if body.get("ids") is not None or body.get("tenant") is not None
                else 32
            )
        filters = []
        if body.get("ids") is not None:
            filters.append(
                "id IN (" + ",".join("'" + str(int(i)) + "'" for i in body["ids"]) + ")"
            )
        if body.get("tenant") is not None:
            filters.append("tenant = " + str(int(body["tenant"])))
        if filters:
            q = q.where(" AND ".join(filters), prefilter=True)
        return q.select(META).limit(body["k"]).to_list()
    if action == "stats":
        t = table()
        return {
            "rows": t.count_rows(),
            "version": t.version,
            "readonly": readonly,
            "indexes": [str(x) for x in t.list_indices()],
        }
    if action == "strength":
        t = table()
        version = t.version
        old = db.open_table("products")
        old.checkout(version)
        row = fixture.row(0, 123456)
        t.merge_insert("id").when_matched_update_all().execute(arrow([row]))
        assert old.search().where("id = '0'").to_list()[0]["version"] == 0
        assert table().search().where("id = '0'").to_list()[0]["version"] == 123456
        tail = (
            table()
            .search(fixture.vectors[0], vector_column_name="embedding")
            .distance_type("cosine")
            .where("id = '0'")
            .limit(1)
            .to_list()
        )
        assert len(tail) == 1 and tail[0]["id"] == "0" and tail[0]["version"] == 123456
        started = time.perf_counter()
        t.optimize(cleanup_older_than=timedelta(days=30))
        compaction_seconds = time.perf_counter() - started
        reopened = db.open_table("products")
        reopened.checkout(version)
        assert reopened.search().where("id = '0'").to_list()[0]["version"] == 0
        t.restore(version)
        assert table().search().where("id = '0'").to_list()[0]["version"] == 0
        return {
            "pinned_version": version,
            "old_reader_preserved": True,
            "latest_update_visible": True,
            "unindexed_tail_visible": True,
            "compaction_seconds": compaction_seconds,
            "retained_versions": len(t.list_versions()),
            "restored_version": table().version,
        }
    if action == "maintenance":
        return {"result": str(table().optimize(cleanup_older_than=timedelta(days=30)))}
    raise ValueError("unknown action")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"ready": True})
            return
        if self.path == "/rows":
            try:
                batches = (
                    table()
                    .search()
                    .select(META)
                    .limit(len(fixture.products) + 1)
                    .to_batches(batch_size=1000)
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                for batch in batches:
                    self.wfile.write(
                        "".join(
                            json.dumps(row) + "\n" for row in batch.to_pylist()
                        ).encode()
                    )
            except Exception:
                traceback.print_exc()
                self.close_connection = True
            return
        self.reply(404, {"error": "unknown endpoint"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 16 * 1024 * 1024:
                raise ValueError("request too large")
            body = json.loads(self.rfile.read(length))
            action = self.path.strip("/")
            if action in ("load", "update", "strength", "maintenance"):
                with write_lock:
                    result = operate(action, body)
            else:
                result = operate(action, body)
            self.reply(200, result)
        except PermissionError as error:
            self.reply(403, {"error": str(error)})
        except Exception as error:
            traceback.print_exc()
            self.reply(500, {"error": str(error)})

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
