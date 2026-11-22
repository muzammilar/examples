import csv
import json
from pathlib import Path
import tempfile
import unittest

import history


class HistoryTests(unittest.TestCase):
    def test_legacy_async_rpo_keeps_source_acknowledgements_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seed-0.jsonl").write_text(
                json.dumps({"first_id": 0, "end_id": 4}) + "\n"
            )
            (root / "fault-0.jsonl").write_text(
                json.dumps({"first_id": 4, "end_id": 6}) + "\n"
            )
            path = root / "legacy.csv"
            with path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    [
                        "event_id",
                        "customer_id",
                        "amount_cents",
                        "batch_start",
                        "batch_end",
                    ]
                )
                writer.writerows((i, i % 2, i + 1, 0, 4) for i in range(4))
            proof = history.reconcile_legacy(
                path, root, ["seed", "fault"], 2, missing_labels=["fault"]
            )
            self.assertEqual(proof["missing_acknowledged_by_label"], {"fault": 2})
            with self.assertRaisesRegex(RuntimeError, "acknowledged rows missing"):
                history.reconcile_legacy(path, root, ["seed", "fault"], 2)

    def test_legacy_full_body_rejects_wrong_customer_and_partial_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "disk-0.jsonl").write_text(
                json.dumps({"first_id": 0, "end_id": 2}) + "\n"
            )
            path = root / "legacy.csv"
            for rows in ([(0, 1, 1, 0, 2), (1, 1, 2, 0, 2)], [(0, 0, 1, 0, 2)]):
                with path.open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(
                        [
                            "event_id",
                            "customer_id",
                            "amount_cents",
                            "batch_start",
                            "batch_end",
                        ]
                    )
                    writer.writerows(rows)
                with self.assertRaises(RuntimeError):
                    history.reconcile_legacy(path, root, ["disk"], 2)

    def test_reboot_cannot_silently_restore_default_arc_maximum(self):
        expected = 256 * 1024**2
        self.assertEqual(
            history.verify_arc(f"c_max 4 {expected}\n", expected, expected), expected
        )
        for text, effective in (
            ("c_max 4 760590336\n", 0),
            (f"c_max 4 {expected}\n", 0),
            ("", expected),
        ):
            with self.assertRaisesRegex(RuntimeError, "effective ZFS ARC"):
                history.verify_arc(text, effective, expected)

    def fixture(self, root):
        config = {"rows": 4, "customers": 2, "clients": 1, "batch_rows": 2}
        intent = {
            "kind": "intent",
            "first": 4,
            "end": 6,
            "journal": "writer-0.jsonl",
            "worker": 0,
            "seq": 1,
        }
        ack = dict(intent, kind="ok", ended_ns=10)
        (root / "writer-0.jsonl").write_text(
            json.dumps(intent) + "\n" + json.dumps(ack) + "\n"
        )
        expected = [history.body(i, 0, 4, 2) for i in (0, 3)] + [
            history.body(i, 4, 6, 2) for i in (4, 5)
        ]
        read = {
            "kind": "ok",
            "expected": expected,
            "actual": expected,
            "started_ns": 20,
            "acknowledged_ended_ns": 10,
        }
        (root / "reader.jsonl").write_text(json.dumps(read) + "\n")
        rows = [history.body(i, 0, 4, 2) for i in range(4)] + [
            history.body(i, 4, 6, 2) for i in (4, 5)
        ]
        self.write_rows(root, rows)
        return config, rows, read

    def write_rows(self, root, rows):
        with (root / "rows.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "event_id",
                    "customer_id",
                    "amount_cents",
                    "payload",
                    "batch_start",
                    "batch_end",
                ]
            )
            writer.writerows(rows)

    def test_complete_body_and_acknowledgement_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, _ = self.fixture(root)
            result = history.reconcile(root / "rows.csv", root, config)
            self.assertEqual(result["acknowledged_rows"], 2)
            self.assertEqual(result["successful_reads_verified"], 1)

    def test_loss_wrong_body_and_partial_batches_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, rows, _ = self.fixture(root)
            for bad in (rows[:-1], rows[:3] + rows[4:], rows[:4]):
                self.write_rows(root, bad)
                with self.assertRaises(RuntimeError):
                    history.reconcile(root / "rows.csv", root, config)
            rows[4][2] += 1
            self.write_rows(root, rows)
            with self.assertRaisesRegex(RuntimeError, "wrong event body"):
                history.reconcile(root / "rows.csv", root, config)

    def test_self_consistent_wrong_reader_records_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, read = self.fixture(root)
            read["expected"][0][2] += 1
            (root / "reader.jsonl").write_text(json.dumps(read) + "\n")
            with self.assertRaisesRegex(RuntimeError, "independent oracle"):
                history.reconcile(root / "rows.csv", root, config)

    def test_unacknowledged_commit_is_not_an_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, _ = self.fixture(root)
            path = root / "writer-0.jsonl"
            path.write_text(
                path.read_text().replace('"kind": "ok"', '"kind": "ambiguous"')
            )
            (root / "reader.jsonl").write_text("")
            result = history.reconcile(root / "rows.csv", root, config)
            self.assertEqual(result["acknowledged_rows"], 0)
            self.assertEqual(result["applied_unacknowledged_batches"], 1)

    def test_missing_final_seed_row_cannot_hide_behind_new_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, rows, _ = self.fixture(root)
            self.write_rows(root, rows[:3] + rows[4:])
            with self.assertRaisesRegex(RuntimeError, "seed rows missing"):
                history.reconcile(root / "rows.csv", root, config)

    def test_known_pre_submission_failure_cannot_have_committed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, _ = self.fixture(root)
            path = root / "writer-0.jsonl"
            path.write_text(path.read_text().replace('"kind": "ok"', '"kind": "fail"'))
            with self.assertRaisesRegex(RuntimeError, "pre-submission"):
                history.reconcile(root / "rows.csv", root, config)

    def test_future_ack_and_partial_journal_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, read = self.fixture(root)
            read["started_ns"] = 5
            (root / "reader.jsonl").write_text(json.dumps(read) + "\n")
            with self.assertRaisesRegex(RuntimeError, "unwitnessed"):
                history.reconcile(root / "rows.csv", root, config)
            path = root / "writer-0.jsonl"
            path.write_text(path.read_text() + '{"kind":')
            with self.assertRaisesRegex(RuntimeError, "incomplete history"):
                history.events(path)
            self.assertEqual(len(history.events(path, complete=False)), 2)

    def test_exclusive_metadata_publication_preserves_prior_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            history.save(path, {"passed": False})
            with self.assertRaises(FileExistsError):
                history.save(path, {"passed": True})
            self.assertEqual(json.loads(path.read_text()), {"passed": False})

    def test_acknowledgement_cursor_keeps_partial_line_for_next_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "writer-0.jsonl"
            cursor = history.Acknowledgements(root)
            path.write_text('{"kind":"ok","ended_ns":1}\n{"kind":"ok",')
            self.assertEqual(cursor.poll()["ended_ns"], 1)
            with path.open("a") as stream:
                stream.write('"ended_ns":2}\n')
            self.assertEqual(cursor.poll()["ended_ns"], 2)
            self.assertEqual(cursor.poll()["ended_ns"], 2)
            path.write_text("")
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                cursor.poll()


if __name__ == "__main__":
    unittest.main()
