"""The independent oracle must reject missing receipts and partial paired updates."""

import csv
import json
from pathlib import Path
import tempfile
import unittest

import mutable_oracle as oracle
import resources


class MutableWitness(unittest.TestCase):
    def test_native_io_records_operation_rates_and_rejects_incomplete_data(self):
        first = resources.parse_native_io("254:0 rbytes=100 wbytes=200 rios=1 wios=2\n")
        second = resources.parse_native_io(
            "254:0 rbytes=500 wbytes=1000 rios=5 wios=10\n"
        )
        samples = []
        for timestamp, values in ((1_000_000_000, first), (3_000_000_000, second)):
            samples.append(
                {
                    "role": "cedar",
                    "ended_ns": timestamp,
                    "stats": {},
                    "disk": {"native_io": values, "native_io_captured_ns": timestamp},
                }
            )
        io = resources.summarize_records(samples)["cedar"]["io_and_network"]
        self.assertEqual(io["native_block_254:0_write_operations"]["per_second"], 4)
        self.assertEqual(io["native_block_254:0_read_bytes"]["observed_delta"], 400)
        self.assertEqual(resources.parse_native_io("254:0\n")["254:0"]["rios"], 0)
        for text in ("254:0 rios=1", "254:0 rbytes=-1 wbytes=0 rios=0 wios=0"):
            with self.assertRaises(ValueError):
                resources.parse_native_io(text)

    def test_snapshot_watermark_cannot_forget_prior_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reader.jsonl"
            writes = [{"kind": "ok", "seq": 2, "ended_ns": 10_000_000}]
            event = {"kind": "ok", "request": 1, "seq": 2, "started_ns": 30_000_000}
            path.write_text(json.dumps(event) + "\n")
            self.assertEqual(
                oracle.verify_reads(path, writes, {1, 2})["successful_snapshots"], 1
            )
            event["seq"] = 1
            path.write_text(json.dumps(event) + "\n")
            with self.assertRaisesRegex(RuntimeError, "predates"):
                oracle.verify_reads(path, writes, {1, 2})
            event["seq"], event["request"] = 2, 2
            path.write_text(json.dumps(event) + "\n")
            with self.assertRaisesRegex(RuntimeError, "request IDs"):
                oracle.verify_reads(path, writes, {1, 2})

    def test_complete_replay_and_negative_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, receipts, journal = (
                root / name for name in ("rows.csv", "receipts.csv", "journal.jsonl")
            )
            events = []
            for seq in (1, 2):
                events.extend(
                    [
                        {
                            "kind": "intent",
                            "seq": seq,
                            "operation": oracle.operation(seq),
                            "rows": [oracle.body(0, seq), oracle.body(100, seq, True)],
                        },
                        {"kind": "ok", "seq": seq},
                    ]
                )
            journal.write_text("\n".join(map(json.dumps, events)) + "\n")
            receipts.write_text(
                "seq,operation,left_id,right_id\n1,paired-update,0,100\n2,delete-reinsert,0,100\n"
            )
            with rows.open("w") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["id", "customer", "amount", "payload", "revision"],
                )
                writer.writeheader()
                for identity in range(200):
                    writer.writerow(
                        oracle.body(identity, 2, identity == 100)
                        if identity in (0, 100)
                        else oracle.body(identity)
                    )
            good_rows, good_receipts = rows.read_text(), receipts.read_text()
            self.assertEqual(
                oracle.reconcile(rows, receipts, journal, 200, 1)[
                    "acknowledged_transactions"
                ],
                2,
            )
            receipts.write_text(good_receipts.replace("1,paired-update,0,100\n", ""))
            with self.assertRaisesRegex(RuntimeError, "receipt missing"):
                oracle.reconcile(rows, receipts, journal, 200, 1)
            receipts.write_text(good_receipts)
            for bad in (
                good_rows.replace(",1697,", ",1698,"),
                "\n".join(good_rows.splitlines()[:-1]) + "\n",
            ):
                rows.write_text(bad)
                with self.assertRaises(RuntimeError):
                    oracle.reconcile(rows, receipts, journal, 200, 1)
            rows.write_text(good_rows)
            events[-1]["kind"] = "ambiguous"
            journal.write_text("\n".join(map(json.dumps, events)) + "\n")
            proof = oracle.reconcile(rows, receipts, journal, 200, 1)
            self.assertEqual(proof["acknowledged_transactions"], 1)
            self.assertEqual(proof["applied_unacknowledged_intents"], 1)


if __name__ == "__main__":
    unittest.main()
