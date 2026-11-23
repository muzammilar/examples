import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import experiment as bench
import ingestion


class IngestionTests(unittest.TestCase):
    def fixture(self, root):
        data = root / "data"
        data.mkdir()
        path = data / "trades.csv"
        with path.open("w") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["id", "customer", "symbol", "day", "qty", "price"])
            writer.writerows(ingestion.trade(i) for i in range(3))
        return path

    def test_full_body_verifier_rejects_changed_old_row_even_when_count_matches(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(bench, "RESULTS", Path(temp)),
        ):
            source = self.fixture(Path(temp))
            expected = ingestion.oracle(3, 2)
            path = Path(temp) / "export.csv"
            body = source.read_bytes() + b"3,3,3,3,4,103\n4,4,4,4,5,104\n"
            path.write_bytes(body)
            report = ingestion.verify_export(path, expected)
            self.assertEqual(report["verified_rows"], 5)
            self.assertGreater(report["compression"]["compressed_bytes"], 0)
            path.write_bytes(body.replace(b"0,0,0,0,1,100", b"0,0,0,0,2,100"))
            with self.assertRaisesRegex(RuntimeError, "full-body export differs"):
                ingestion.verify_export(path, expected)
            self.assertTrue(path.exists())

    def test_query_and_native_plans_cover_identical_ids_with_partial_last_batch(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(bench, "RESULTS", Path(temp)),
        ):
            self.fixture(Path(temp))
            for method in ("query", "native", "columnar"):
                path = Path(temp) / (method + ".tsv")
                records = ingestion.write_plan(path, "duckdb", method, 3, 5, 2)
                rows = [line.split("\t") for line in path.read_text().splitlines()]
                self.assertEqual(records, len(rows) + 1)
                self.assertEqual(
                    sum(r[0] in {"append", "write", "columnar"} for r in rows), 3
                )
                self.assertEqual(
                    sum(r[0] in {"reject-append", "reject-columnar"} for r in rows), 4
                )
                if method in {"native", "columnar"}:
                    self.assertEqual(
                        [r[3] for r in rows if r[0] in {"append", "columnar"}],
                        ["3 2", "5 2", "7 1"],
                    )
                else:
                    last = [r[3] for r in rows if r[0] == "write"][-1]
                    self.assertEqual(last, "INSERT INTO trades VALUES (7,7,7,7,8,107)")

    def test_middle_read_occurs_between_two_write_requests(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(bench, "RESULTS", Path(temp)),
        ):
            self.fixture(Path(temp))
            path = Path(temp) / "two-batches.tsv"
            ingestion.write_plan(path, "duckdb", "columnar", 3, 10001, 10000)
            rows = [line.split("\t") for line in path.read_text().splitlines()]
            writes = [i for i, row in enumerate(rows) if row[0] == "columnar"]
            middle = next(
                i for i, row in enumerate(rows) if row[1] == "middle-readback"
            )
            self.assertLess(writes[0], middle)
            self.assertLess(middle, writes[1])
            self.assertEqual(rows[writes[1]][3], "10003 1")

    def test_rayforce_uses_quoted_mutation_instead_of_functional_copy(self):
        self.assertEqual(
            ingestion.insert("rayforce", [ingestion.trade(3)]),
            "(insert 'trades (list [3] [3] [3] [3] [4] [103]))",
        )

    def test_throughput_uses_observed_window_not_sum_of_call_latencies(self):
        records = [
            {"phase": "write", "elapsed_ns": 3000000},
            {"phase": "write", "elapsed_ns": 4000000},
            {
                "phase": "window",
                "query": "ingestion",
                "elapsed_ns": 10000000,
                "cpu_ns": 7000000,
            },
        ]
        result = ingestion.write_summary({"records": records}, 100)
        self.assertEqual(result["rows_per_second"], 10000)
        self.assertEqual(result["native_call_seconds"], 0.007)


if __name__ == "__main__":
    unittest.main()
