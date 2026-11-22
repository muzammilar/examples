"""Actual host-process exclusion control, independent of the Docker VM kernel."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import run


class HostControllerLock(unittest.TestCase):
    def test_competing_process_is_rejected_then_acquires_same_inode(self):
        source = """
import json,sys
from pathlib import Path
import run
try:
    with run.lock(Path(sys.argv[1])) as identity:
        print(json.dumps({'locked':True,'inode':identity.st_ino,'device':identity.st_dev}))
except RuntimeError as error:
    print(json.dumps({'locked':False,'error':str(error)}))
"""
        run.RESULTS.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=run.RESULTS) as directory:
            path = Path(directory) / "controller.lock"

            def probe():
                result = subprocess.run(
                    [sys.executable, "-c", source, str(path)],
                    cwd=run.ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return json.loads(result.stdout)

            with run.lock(path) as identity:
                rejected = probe()
                self.assertFalse(rejected["locked"])
                self.assertEqual(
                    rejected["error"], "another example lifecycle command is active"
                )
                inode, device = identity.st_ino, identity.st_dev
            acquired = probe()
            self.assertTrue(acquired["locked"])
            self.assertEqual((acquired["inode"], acquired["device"]), (inode, device))

    def test_lock_does_not_follow_a_symlink(self):
        run.RESULTS.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=run.RESULTS) as directory:
            path = Path(directory)
            (path / "target").touch()
            (path / "controller.lock").symlink_to(path / "target")
            with self.assertRaises(OSError):
                with run.lock(path / "controller.lock"):
                    self.fail("symlink lock acquired")


if __name__ == "__main__":
    unittest.main()
