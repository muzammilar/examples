"""Retired trial histories must not become the next lab's active corpus."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "kernel_project", ROOT / "projects/kernel-client/control.py"
)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


class FixtureOwnership(unittest.TestCase):
    def test_old_trials_are_preserved_but_excluded(self):
        (ROOT / ".build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".build") as temporary:
            root = Path(temporary)
            for name, parent, fsid in (
                ("old", "retired", "old-fsid"),
                ("current", "active", "active-fsid"),
            ):
                directory = root / "results/kernel-client/trials" / name
                directory.mkdir(parents=True)
                (directory / "fixture.json").write_text(
                    json.dumps({"parent_fixture": parent, "fsid": fsid})
                )
            with patch.object(kernel, "ROOT", root), patch.object(
                kernel.native_checks,
                "fixture",
                return_value={"id": "active", "fsid": "active-fsid"},
            ):
                selected = list(kernel.trial_fixtures())
                self.assertEqual(
                    [path.parent.name for path, _ in selected], ["current"]
                )
                self.assertTrue(
                    (root / "results/kernel-client/trials/old/fixture.json").exists()
                )
                (selected[0][0]).write_text(
                    json.dumps({"parent_fixture": "active", "fsid": "foreign"})
                )
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    list(kernel.trial_fixtures())


if __name__ == "__main__":
    unittest.main()
