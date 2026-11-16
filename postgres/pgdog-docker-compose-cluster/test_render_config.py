import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RenderSafety(unittest.TestCase):
    def test_invalid_size_preserves_existing_configuration(self):
        script = Path(__file__).parent / "scripts/render-config.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            existing = root / "config/pgdog.toml"
            existing.write_text("keep this live configuration")
            for size in ("0", "129", "wrong"):
                result = subprocess.run(
                    [sys.executable, str(script.resolve())],
                    cwd=root,
                    env=dict(os.environ, POOL_SIZE=size),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(existing.read_text(), "keep this live configuration")
