"""Routing failure boundaries, independent of native replication integration tests."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sharded import RoutedAdapter, shard_for


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RoutedAdapter(
            SimpleNamespace(products=[{"tenant": i} for i in range(6)])
        )
        self.adapter.shards = [Mock() for _ in range(3)]
        self.addCleanup(self.adapter.pool.shutdown)

    def test_customer_filter_routes_one_shard(self):
        self.adapter.search("x", None, "hybrid", tenant=4)
        self.adapter.shards[1].search.assert_called_once_with(
            "x", None, "hybrid", 10, None, 4
        )
        self.adapter.shards[0].search.assert_not_called()
        self.adapter.shards[2].search.assert_not_called()
        for invalid in [-1, "1", True]:
            with self.assertRaises(ValueError):
                shard_for(invalid)

    def test_global_distance_merge_and_failed_shard(self):
        for i, shard in enumerate(self.adapter.shards):
            shard.search.return_value = [{"id": str(i), "score": 3 - i}]
        self.assertEqual(
            [hit["id"] for hit in self.adapter.search("", None, "dense", k=2)],
            ["2", "1"],
        )
        self.adapter.shards[1].search.side_effect = RuntimeError("unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.adapter.search("", None, "dense")
        with self.assertRaisesRegex(ValueError, "global lexical/hybrid"):
            self.adapter.search("", None, "hybrid")

    def test_cross_shard_failure_is_not_replayed(self):
        self.adapter.shards[1].update.side_effect = RuntimeError(
            "unknown commit outcome"
        )
        rows = [{"id": str(i), "tenant": i} for i in range(3)]
        with self.assertRaises(RuntimeError):
            self.adapter.update(rows)
        self.adapter.shards[0].update.assert_called_once_with([rows[0]])
        self.adapter.shards[1].update.assert_called_once_with([rows[1]])
        self.adapter.shards[2].update.assert_not_called()
        with self.assertRaisesRegex(ValueError, "migration"):
            self.adapter.update([{"id": "0", "tenant": 1}])

    def test_wrong_shard_is_rejected(self):
        self.adapter.shards[0].rows.return_value = iter([{"id": "1", "tenant": 1}])
        with self.assertRaisesRegex(RuntimeError, "wrong shard"):
            list(self.adapter.rows())
