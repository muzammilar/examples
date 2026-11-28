import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import archive


class InputTape(unittest.TestCase):
    def fixture(self, root):
        folder = root / "results/source-1"
        folder.mkdir(parents=True)
        (folder / "fixture.json").write_text(json.dumps({"id": "a" * 32}))
        (folder / "orders-source-fence.txt").write_text(
            "".join(f"cdc.public.orders:{i}:1\n" for i in range(4))
        )
        (folder / "customers-source-fence.txt").write_text("cdc.public.customers:0:1\n")
        output = root / "results/down-1"
        output.mkdir()
        lines = []
        for topic, part in [("cdc.public.customers", 0)] + [
            ("cdc.public.orders", i) for i in range(4)
        ]:
            lines.append(
                json.dumps(
                    {
                        "topic": topic,
                        "partition": part,
                        "offset": "0",
                        "payload": json.dumps(
                            {
                                "op": "c",
                                "after": {"id": part + 1},
                                "source": {"lsn": 12},
                            }
                        ),
                    }
                ).encode()
                + b"\n"
            )
        return output, lines

    def execute(self, root, output, lines, code=0):
        child = MagicMock()
        child.stdout = io.BytesIO(b"".join(lines))
        child.wait.return_value = code
        child.poll.return_value = code
        with patch.object(archive.subprocess, "Popen", return_value=child):
            return archive.preserve(root, root, ["docker"], output, "a" * 32)

    def test_exact_stream_and_compressed_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, lines = self.fixture(root)
            result = self.execute(root, output, lines)
            packed = (output / "consumed-cdc.jsonl.gz").read_bytes()
            self.assertEqual(gzip.decompress(packed), b"".join(lines))
            self.assertEqual(result["unique_positions"], 5)
            self.assertEqual(result["sha256"], hashlib.sha256(packed).hexdigest())

    def test_missing_prefix_is_not_a_complete_tape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, lines = self.fixture(root)
            lines = lines[1:]
            with self.assertRaisesRegex(RuntimeError, "Incomplete source archive"):
                self.execute(root, output, lines)
            self.assertFalse((output / "source-archive.json").exists())

    def test_conflicting_duplicate_position_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, lines = self.fixture(root)
            changed = json.loads(lines[0])
            changed["payload"] = json.dumps({"op": "d"})
            lines.insert(1, json.dumps(changed).encode() + b"\n")
            with self.assertRaisesRegex(RuntimeError, "Conflicting payload"):
                self.execute(root, output, lines)

    def test_native_failure_cannot_publish_valid_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, lines = self.fixture(root)
            with self.assertRaisesRegex(RuntimeError, "Native source export failed"):
                self.execute(root, output, lines, 1)
            self.assertFalse((output / "source-archive.json").exists())
