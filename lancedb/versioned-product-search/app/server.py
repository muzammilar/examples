"""Lab SDK adapter: one writer and independent read-only views of one catalog."""

from datetime import timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import threading
import time
import traceback
from urllib.parse import urlsplit

import lancedb
from lancedb.index import BTree, FTS, IvfFlat
import numpy as np
import pyarrow as pa
from workload import Fixture, META

MUTABLE = ["id", "tenant", "stock", "price_cents", "version"]
MUTATIONS = {"load", "update", "strength", "maintenance", "drop"}


def fingerprint(fixture):
    payload = {key: fixture.manifest[key] for key in ["parameters", "sha256"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Catalog:
    def __init__(self, fixture, path="/data/catalog", readonly=False):
        self.fixture = fixture
        self.db = lancedb.connect(path, read_consistency_interval=timedelta(0))
        self.readonly = readonly
        self.fingerprint = fingerprint(fixture)
        self.write_lock = threading.Lock()
        self.counter_lock = threading.Lock()
        self.pending = 0

    def table(self):
        return self.db.open_table("products")

    def snapshot(self):
        table = self.table()
        version = table.version
        table.checkout(version)
        return table, version

    def arrow(self, rows):
        ids = [int(row["id"]) for row in rows]
        arrays = {key: pa.array([row[key] for row in rows]) for key in META}
        arrays["embedding"] = pa.FixedSizeListArray.from_arrays(
            pa.array(self.fixture.vectors[ids].ravel(), type=pa.float32()), 384
        )
        arrays["text"] = pa.array(
            [
                row["name"] + " " + row["brand"] + " " + row["description"]
                for row in rows
            ]
        )
        return pa.table(arrays).replace_schema_metadata(
            {b"fixture_sha256": self.fingerprint.encode()}
        )

    def bound(self, table):
        if (table.schema.metadata or {}).get(
            b"fixture_sha256"
        ) != self.fingerprint.encode():
            raise RuntimeError(
                "existing catalog does not match this exact input artifact"
            )

    def handle(self, action, body):
        if action not in MUTATIONS:
            return self.operate(action, body)
        if self.readonly:
            raise PermissionError("reader is read-only")
        with self.counter_lock:
            self.pending += 1
        try:
            with self.write_lock:
                return self.operate(action, body)
        finally:
            with self.counter_lock:
                self.pending -= 1

    def validated_updates(self, rows):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
            raise ValueError("expected 1..100 inventory rows")
        seen = set()
        result = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(MUTABLE):
                raise ValueError("inventory mutation fields differ")
            pid = row["id"]
            version = row["version"]
            if (
                not isinstance(pid, str)
                or not pid.isascii()
                or not pid.isdecimal()
                or str(int(pid)) != pid
                or not 0 <= int(pid) < len(self.fixture.products)
            ):
                raise ValueError("invalid product ID")
            if (
                any(type(row[key]) is not int for key in MUTABLE if key != "id")
                or not 0 < version < 100000000
            ):
                raise ValueError("invalid inventory types/version")
            expected = self.fixture.row(int(pid), version)
            if row != {key: expected[key] for key in MUTABLE} or pid in seen:
                raise ValueError("wrong/duplicate inventory body")
            seen.add(pid)
            result.append(expected)
        return result

    def operate(self, action, body):
        if action == "load":
            if "products" in self.db.table_names():
                table = self.table()
                self.bound(table)
                return {"retained": True, "catalog_version": table.version}
            started = time.perf_counter()
            table = self.db.create_table(
                "products",
                data=self.arrow(self.fixture.products),
                mode="create",
                on_bad_vectors="error",
            )
            inserted = time.perf_counter() - started
            table.create_index(
                "embedding", config=IvfFlat(distance_type="cosine", num_partitions=256)
            )
            table.create_index("tenant", config=BTree())
            table.create_index("text", config=FTS(stem=True, remove_stop_words=True))
            return {
                "retained": False,
                "insert_seconds": inserted,
                "index_seconds": time.perf_counter() - started - inserted,
                "catalog_version": table.version,
            }
        if action == "update":
            rows = self.validated_updates(body.get("rows"))
            table = self.table()
            self.bound(table)
            result = (
                table.merge_insert("id")
                .when_matched_update_all(
                    where="target.version < source.version AND target.tenant = source.tenant"
                )
                .execute(self.arrow(rows))
            )
            if (
                result.num_updated_rows != len(rows)
                or result.num_inserted_rows
                or result.num_deleted_rows
            ):
                raise RuntimeError(
                    "conditional update did not update every expected row; do not acknowledge"
                )
            return {
                "updated": result.num_updated_rows,
                "catalog_version": result.version,
                "native_attempts": result.num_attempts,
            }
        if action == "search":
            table, version = self.snapshot()
            self.bound(table)
            mode = body["mode"]
            if mode == "lexical":
                query = table.search(body["text"], query_type="fts", fts_columns="text")
            elif mode == "hybrid":
                query = (
                    table.search(
                        query_type="hybrid",
                        vector_column_name="embedding",
                        fts_columns="text",
                    )
                    .vector(body["vector"])
                    .text(body["text"])
                )
            elif mode == "dense":
                query = table.search(body["vector"], vector_column_name="embedding")
            else:
                raise ValueError("unknown retrieval mode")
            if mode != "lexical":
                query = query.distance_type("cosine").nprobes(
                    256
                    if body.get("ids") is not None or body.get("tenant") is not None
                    else 32
                )
            filters = []
            if body.get("ids") is not None:
                filters.append(
                    "id IN ("
                    + ",".join("'" + str(int(pid)) + "'" for pid in body["ids"])
                    + ")"
                )
            if body.get("tenant") is not None:
                filters.append("tenant = " + str(int(body["tenant"])))
            if filters:
                query = query.where(" AND ".join(filters), prefilter=True)
            count = int(body["k"])
            if not 1 <= count <= 100:
                raise ValueError("invalid result limit")
            return {
                "hits": query.select(META).limit(count).to_list(),
                "catalog_version": version,
            }
        if action == "stats":
            table, version = self.snapshot()
            self.bound(table)
            with self.counter_lock:
                pending = self.pending
            return {
                "rows": table.count_rows(),
                "catalog_version": version,
                "readonly": self.readonly,
                "pending_mutations": pending,
                "fixture_sha256": self.fingerprint,
                "lancedb_version": lancedb.__version__,
                "indexes": [
                    {"name": i.name, "columns": i.columns, "type": str(i.index_type)}
                    for i in table.list_indices()
                ],
            }
        if action == "strength":
            table = self.table()
            self.bound(table)
            version = table.version
            old = self.table()
            old.checkout(version)
            original = old.search().where("id = '0'").select(META).to_list()[0]
            temporary = self.fixture.row(0, original["version"] + 1)
            try:
                table.merge_insert("id").when_matched_update_all().execute(
                    self.arrow([temporary])
                )
                if old.search().where("id = '0'").select(META).to_list()[0] != original:
                    raise RuntimeError("pinned snapshot changed")
                if (
                    self.table().search().where("id = '0'").select(META).to_list()[0]
                    != temporary
                ):
                    raise RuntimeError("latest update invisible")
                tail = (
                    self.table()
                    .search(self.fixture.vectors[0], vector_column_name="embedding")
                    .distance_type("cosine")
                    .where("id = '0'")
                    .limit(1)
                    .to_list()
                )
                if len(tail) != 1 or tail[0]["version"] != temporary["version"]:
                    raise RuntimeError("unindexed vector tail invisible")
                started = time.perf_counter()
                table.optimize(cleanup_older_than=timedelta(days=30))
                duration = time.perf_counter() - started
                reopened = self.table()
                reopened.checkout(version)
                if (
                    reopened.search().where("id = '0'").select(META).to_list()[0]
                    != original
                ):
                    raise RuntimeError("compaction lost pinned snapshot")
            finally:
                table.restore(version)
            if (
                self.table().search().where("id = '0'").select(META).to_list()[0]
                != original
            ):
                raise RuntimeError(
                    "snapshot restore changed the original acknowledged body"
                )
            return {
                "pinned_version": version,
                "restored_catalog_version": self.table().version,
                "old_reader_preserved": True,
                "latest_update_visible": True,
                "unindexed_tail_visible": True,
                "compaction_seconds": duration,
                "restored_prior_body": True,
            }
        if action == "maintenance":
            return {
                "result": str(
                    self.table().optimize(cleanup_older_than=timedelta(days=30))
                ),
                "catalog_version": self.table().version,
            }
        if action == "drop":
            if "products" in self.db.table_names():
                self.db.drop_table("products")
            return {"dropped": True}
        raise ValueError("unknown action")

    def point(self, pid):
        if (
            not pid.isascii()
            or not pid.isdecimal()
            or str(int(pid)) != pid
            or not 0 <= int(pid) < len(self.fixture.products)
        ):
            raise ValueError("invalid product ID")
        table, version = self.snapshot()
        self.bound(table)
        rows = (
            table.search()
            .where("id = '" + pid + "'")
            .select(MUTABLE)
            .limit(2)
            .to_list()
        )
        if len(rows) != 1:
            raise LookupError("missing/duplicate known product")
        return {"row": rows[0], "catalog_version": version}


class Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, address, catalog):
        super().__init__(address, Handler)
        self.catalog = catalog
        self.stopping = threading.Event()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        self.connection.settimeout(2)
        super().handle_one_request()

    def ready(self):
        self.connection.settimeout(30)
        if self.server.stopping.is_set():
            self.close_connection = True
            self.reply(503, {"error": "server shutting down"})
            return False
        return True

    def do_GET(self):
        if not self.ready():
            return
        path = urlsplit(self.path)
        try:
            if path.path == "/health":
                self.reply(200, {"ready": self.server.catalog.db.is_open()})
                return
            if path.path.startswith("/inventory/"):
                self.reply(
                    200,
                    self.server.catalog.point(path.path.removeprefix("/inventory/")),
                )
                return
            if path.path == "/rows":
                table, version = self.server.catalog.snapshot()
                self.server.catalog.bound(table)
                columns = META + (["embedding"] if path.query == "vectors=hash" else [])
                batches = (
                    table.search()
                    .select(columns)
                    .limit(len(self.server.catalog.fixture.products) + 1)
                    .to_batches(batch_size=1000)
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("X-Catalog-Version", str(version))
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                for batch in batches:
                    for row in batch.to_pylist():
                        if "embedding" in row:
                            values = np.asarray(row.pop("embedding"), dtype="<f4")
                            if values.shape != (384,):
                                raise RuntimeError("invalid stored vector shape")
                            row["vector_sha256"] = hashlib.sha256(
                                values.tobytes()
                            ).hexdigest()
                        self.wfile.write((json.dumps(row) + "\n").encode())
                return
            self.reply(404, {"error": "unknown endpoint"})
        except LookupError as error:
            self.reply(404, {"error": str(error)})
        except Exception as error:
            traceback.print_exc()
            self.close_connection = True
            if path.path != "/rows":
                self.reply(500, {"error": str(error)})

    def do_POST(self):
        if not self.ready():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16 * 1024 * 1024:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("expected object body")
            self.reply(200, self.server.catalog.handle(self.path.strip("/"), body))
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


def main():
    catalog = Catalog(Fixture(), readonly=os.environ.get("READ_ONLY") == "1")
    server = Server(("0.0.0.0", 8080), catalog)

    def stop(*_):
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        catalog.db.close()


if __name__ == "__main__":
    main()
