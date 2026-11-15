import unittest
import tempfile
from pathlib import Path
import httpx
from workload import HTTP, ndcg, oracle, read_records, recovery_metrics


class Fixture:
    products = [
        {
            "id": "0",
            "source_id": "a",
            "locale": "us",
            "name": "x",
            "description": "",
            "brand": "b",
            "color": "c",
            "tenant": 1,
            "stock": 1,
            "price_cents": 100,
            "version": 0,
        }
    ]

    def row(self, i, version=0):
        row = self.products[i].copy()
        if version:
            row.update(version=version, stock=version % 100, price_cents=100 + version)
        return row


class Rows:
    def __init__(self, rows):
        self.values = rows

    def rows(self):
        return iter(self.values)


class ContractTests(unittest.TestCase):
    def test_unicode_separators_inside_json_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            path.write_text(
                '{"text":"alpha\u2028beta\u0085gamma"}\n{"text":"second"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                [x["text"] for x in read_records(path)],
                ["alpha\u2028beta\u0085gamma", "second"],
            )

    def test_ack_loss_and_duplicate_detection(self):
        f = Fixture()
        for rows in [[], [f.row(0), f.row(0)]]:
            with self.assertRaises(RuntimeError):
                oracle(Rows(rows), f)
        with self.assertRaises(RuntimeError):
            oracle(Rows([f.row(0, 1)]), f, {0: 2}, {0: 3})
        self.assertEqual(
            oracle(Rows([f.row(0, 3)]), f, {0: 2}, {0: 3})[
                "missing_acknowledged_versions"
            ],
            0,
        )
        with self.assertRaises(RuntimeError):
            oracle(Rows([f.row(0, 4)]), f, {0: 2}, {0: 3})

    def test_corrupt_payload_is_rejected(self):
        f = Fixture()
        row = f.row(0)
        row["tenant"] = 2
        with self.assertRaises(RuntimeError):
            oracle(Rows([row]), f)

    def test_mutation_is_never_replayed(self):
        calls = []

        def send(request):
            calls.append(str(request.url))
            raise httpx.ReadTimeout("commit outcome unknown", request=request)

        h = HTTP(["http://one", "http://two"])
        h.client.close()
        h.client = httpx.Client(transport=httpx.MockTransport(send))
        with self.assertRaisesRegex(RuntimeError, "no mutation replay"):
            h.request("POST", "/update", json={})
        self.assertEqual(calls, ["http://one/update"])

    def test_read_can_move_to_survivor(self):
        def send(request):
            if request.url.host == "one":
                raise httpx.ConnectError("dead", request=request)
            return httpx.Response(200, json={"ok": True})

        h = HTTP(["http://one", "http://two"])
        h.client.close()
        h.client = httpx.Client(transport=httpx.MockTransport(send))
        self.assertTrue(h.request("POST", "/search", read=True, json={}).json()["ok"])

    def test_recovery_ignores_ack_before_failure(self):
        records = [
            {"stage": "attempt", "monotonic": 9},
            {"stage": "ack", "monotonic": 9.1},
            {"stage": "attempt", "monotonic": 10.01},
            {"stage": "ack", "monotonic": 10.02},
            {"stage": "attempt", "monotonic": 10.1},
            {"stage": "ambiguous"},
            {"stage": "attempt", "monotonic": 30.7},
            {"stage": "ack", "monotonic": 30.8},
            {"stage": "search", "monotonic": 30.9},
        ]
        result = recovery_metrics(
            records, {"fault_monotonic": 10, "restart_monotonic": 40}
        )
        self.assertEqual(result["acks_while_node_absent"], 1)
        self.assertAlmostEqual(result["write_recovery_seconds"], 20.7)
        late_failure = [
            {"stage": "attempt", "monotonic": 9},
            {"stage": "ack", "monotonic": 9.1},
            {"stage": "attempt", "monotonic": 11},
            {"stage": "ack", "monotonic": 11.1},
            {"stage": "attempt", "monotonic": 41},
            {"stage": "ambiguous"},
            {"stage": "attempt", "monotonic": 42},
            {"stage": "ack", "monotonic": 42.1},
        ]
        self.assertEqual(
            recovery_metrics(
                late_failure, {"fault_monotonic": 10, "restart_monotonic": 40}
            )["acks_while_node_absent"],
            1,
        )
        with self.assertRaisesRegex(RuntimeError, "same Linux monotonic clock"):
            recovery_metrics(
                records, {"fault_monotonic": 1000, "restart_monotonic": 1030}
            )

    def test_candidate_ndcg(self):
        self.assertEqual(ndcg({"a": "E", "b": "I"}, ["a", "b"], 10), 1)
        self.assertLess(ndcg({"a": "E", "b": "I"}, ["b", "a"], 10), 1)
        self.assertIsNone(ndcg({"a": "I"}, ["a"], 10))


if __name__ == "__main__":
    unittest.main()
