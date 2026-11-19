"""Native SDK negative controls; all storage is temporary and example-owned."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from server import Catalog
from production import check_row, histories


class Fixture:
    def __init__(self):
        self.manifest = {"parameters": {"documents": 20}, "sha256": {"fixture": "test"}}
        self.products = [self.row(i, 0) for i in range(20)]
        self.vectors = np.zeros((20, 384), dtype=np.float32)
        self.vectors[:, 0] = 1

    def row(self, i, version):
        return {
            "id": str(i),
            "source_id": str(i),
            "locale": "us",
            "name": "product",
            "description": "test",
            "brand": "brand",
            "color": "blue",
            "tenant": i % 2,
            "stock": 100 - version % 10,
            "price_cents": 100 + version,
            "version": version,
        }


class NativeControls(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = Fixture()
        self.catalog = Catalog(self.fixture, self.directory.name + "/catalog")
        self.catalog.db.create_table(
            "products", self.catalog.arrow(self.fixture.products)
        )

    def tearDown(self):
        self.catalog.db.close()
        self.directory.cleanup()

    def mutation(self, version=1):
        row = self.fixture.row(0, version)
        return {k: row[k] for k in ["id", "tenant", "stock", "price_cents", "version"]}

    def test_conditional_update_and_duplicate_rejection(self):
        result = self.catalog.handle("update", {"rows": [self.mutation()]})
        self.assertEqual(result["updated"], 1)
        self.assertGreater(result["catalog_version"], 1)
        self.assertGreaterEqual(result["native_attempts"], 1)
        with self.assertRaises(RuntimeError):
            self.catalog.handle("update", {"rows": [self.mutation()]})
        self.assertEqual(self.catalog.point("0")["row"], self.mutation())
        self.catalog.handle("update", {"rows": [self.mutation(2)]})
        self.assertEqual(self.catalog.point("0")["row"], self.mutation(2))
        with self.assertRaises(RuntimeError):
            self.catalog.handle("update", {"rows": [self.mutation(1)]})

    def test_invalid_body_and_readonly(self):
        row = self.mutation()
        row["stock"] = None
        with self.assertRaises(ValueError):
            self.catalog.validated_updates([row])
        with self.assertRaises(ValueError):
            self.catalog.validated_updates([self.mutation()] * 2)
        self.catalog.readonly = True
        with self.assertRaises(PermissionError):
            self.catalog.handle("update", {"rows": [self.mutation()]})

    def test_strength_restores_current_acknowledged_version(self):
        self.catalog.handle("update", {"rows": [self.mutation(7)]})
        result = self.catalog.handle("strength", {})
        self.assertTrue(result["restored_prior_body"])
        self.assertEqual(self.catalog.point("0")["row"], self.mutation(7))

    def test_fixture_identity(self):
        self.catalog.fingerprint = "different"
        with self.assertRaises(RuntimeError):
            self.catalog.bound(self.catalog.table())

    def test_complete_row_negative_controls(self):
        import hashlib

        row = self.fixture.row(0, 0) | {
            "vector_sha256": hashlib.sha256(
                self.fixture.vectors[0].astype("<f4").tobytes()
            ).hexdigest()
        }
        self.assertEqual(check_row(self.fixture, row, {}, {}), 0)
        for change in [{"version": -1}, {"stock": None}, {"vector_sha256": "0" * 64}]:
            with self.assertRaises(RuntimeError):
                check_row(self.fixture, row | change, {}, {})
        with self.assertRaises(RuntimeError):
            check_row(self.fixture, row, {}, {"0": 1})

    def test_live_barrier_does_not_chase_moving_writer(self):
        from production import wait_healthy

        def state(writer, reader):
            base = {
                "readonly": False,
                "rows": 100000,
                "catalog_version": writer,
                "lancedb_version": "0.41.0",
                "fixture_sha256": "fixture",
                "indexes": [
                    {"columns": [key]} for key in ["embedding", "tenant", "text"]
                ],
            }
            return {
                "writer": base,
                "reader1": base | {"readonly": True, "catalog_version": reader},
            }

        with (
            patch("production.NODES", ["writer", "reader1"]),
            patch("production.state", side_effect=[state(20, 19), state(21, 20)]),
            patch("production.time.sleep"),
        ):
            # A barrier can be captured only after membership/roles are valid; views need not already match.
            result = wait_healthy()
        self.assertEqual(result["catalog_barrier"]["version"], 20)
        self.assertEqual(result["views"]["writer"]["catalog_version"], 21)

    def test_http_export_reads_stored_vectors(self):
        import threading
        import urllib.request
        import pyarrow as pa
        from server import Server

        row = self.fixture.products[0]
        data = self.catalog.arrow([row])
        wrong = np.zeros((1, 384), dtype=np.float32)
        wrong[0, 1] = 1
        data = data.set_column(
            data.schema.get_field_index("embedding"),
            "embedding",
            pa.FixedSizeListArray.from_arrays(pa.array(wrong.ravel()), 384),
        )
        self.catalog.table().merge_insert("id").when_matched_update_all().execute(data)
        server = Server(("127.0.0.1", 0), self.catalog)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/rows?vectors=hash", timeout=10
            ) as reply:
                rows = [json.loads(line) for line in reply]
                self.assertEqual(
                    int(reply.headers["X-Catalog-Version"]),
                    self.catalog.table().version,
                )
            self.assertEqual(len(rows), 20)
            for exported in rows:
                if exported["id"] == "0":
                    with self.assertRaises(RuntimeError):
                        check_row(self.fixture, exported, {}, {})
                else:
                    check_row(self.fixture, exported, {}, {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

    def test_timed_search_rejects_wrong_stale_and_short_replies(self):
        import threading
        from client import Adapter
        from unittest.mock import Mock

        adapter = Adapter.__new__(Adapter)
        adapter.fixture = self.fixture
        adapter.expected = {"0": self.fixture.row(0, 2)}
        adapter.validated_hits = 0
        adapter.validation_lock = threading.Lock()
        adapter.http = Mock()
        valid = self.fixture.row(0, 2)
        for hits in [
            [valid | {"stock": None}],
            [self.fixture.products[0]],
            [],
            [valid, valid],
        ]:
            adapter.http.request.return_value.json.return_value = {
                "hits": hits,
                "catalog_version": 3,
            }
            with self.assertRaises(RuntimeError):
                adapter.search("test", self.fixture.vectors[0], "dense", 1)
        adapter.http.request.return_value.json.return_value = {
            "hits": [valid],
            "catalog_version": 3,
        }
        self.assertEqual(
            adapter.search("test", self.fixture.vectors[0], "dense", 1), [valid]
        )
        self.assertEqual(adapter.validated_hits, 1)

    def test_witness_outcome_and_orphan(self):
        root = Path(self.directory.name) / "results"
        case = root / "cases" / "case"
        case.mkdir(parents=True)
        event = {"request_id": "r", "stage": "attempt", "rows": [self.mutation()]}
        file = case / "writer.jsonl"
        file.write_text(json.dumps(event) + "\n")
        with patch("production.ROOT", root):
            self.assertEqual(histories()[2]["ambiguous_rows"], 1)
            file.write_text(json.dumps(event | {"stage": "ack"}) + "\n")
            with self.assertRaises(RuntimeError):
                histories()


if __name__ == "__main__":
    unittest.main()
