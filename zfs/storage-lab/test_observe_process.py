"""Exercise the actual independent reader process with a deliberate body edit."""

import hashlib
import json
from pathlib import Path
import subprocess
import queue
import threading
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class ReaderProcess(unittest.TestCase):
    def test_reader_reports_body_corruption_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = root / "immutable.bin"
            body.write_bytes(b"known bytes" * 100)
            expected = {
                "bytes": body.stat().st_size,
                "sha256": hashlib.sha256(body.read_bytes()).hexdigest(),
            }
            control = root / "reader"
            control.with_suffix(".phase").write_text("baseline")
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "app/observe.py"),
                    "read",
                    str(body),
                    json.dumps(expected),
                    str(control),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            lines = queue.Queue()
            records = []

            def receive():
                for line in process.stdout:
                    value = json.loads(line)
                    records.append(value)
                    lines.put(value)

            collector = threading.Thread(target=receive, daemon=True)
            collector.start()
            try:
                first = lines.get(timeout=5)
                self.assertEqual(first["result"], "ok")
                self.assertEqual(first["actual"], expected)
                self.assertGreaterEqual(
                    first["ended_monotonic_ns"], first["started_monotonic_ns"]
                )
                body.write_bytes(b"wrong bytes" * 100)
                second = lines.get(timeout=5)
                self.assertEqual(second["result"], "mismatch")
            finally:
                control.with_suffix(".stop").touch()
                process.wait(timeout=5)
                collector.join(5)
            self.assertFalse(collector.is_alive())
            self.assertEqual(process.returncode, 0, process.stderr.read())
            self.assertEqual(records[-1]["kind"], "reader-stopped")
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
