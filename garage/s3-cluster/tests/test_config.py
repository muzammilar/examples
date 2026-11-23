"""Cold startup must have explicit peer discovery after identities are known."""

import json
import os
import runpy
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/config.py"


class BootstrapPeers(unittest.TestCase):
    def test_initial_and_known_identity_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"GARAGE_CONFIG_DIR": str(root), "RESULTS_DIR": str(root / "results")}
            with patch.dict(os.environ, env):
                runpy.run_path(str(SCRIPT))
                self.assertEqual(
                    tomllib.loads((root / "garage0.toml").read_text())[
                        "bootstrap_peers"
                    ],
                    [],
                )
                peers = [bytes([i]).hex() * 32 + f"@garage{i}:3901" for i in range(9)]
                (root / "bootstrap.json").write_text(json.dumps({"peers": peers}))
                runpy.run_path(str(SCRIPT))
                for i in range(9):
                    value = tomllib.loads((root / f"garage{i}.toml").read_text())
                    self.assertEqual(
                        value["bootstrap_peers"],
                        [peer for n, peer in enumerate(peers) if n != i],
                    )

    def test_foreign_service_address_fails_before_configuration_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            peers = [bytes([i]).hex() * 32 + f"@garage{i}:3901" for i in range(9)]
            peers[0] = bytes([0]).hex() * 32 + "@other-example:3901"
            (root / "bootstrap.json").write_text(json.dumps({"peers": peers}))
            with (
                patch.dict(os.environ, {"GARAGE_CONFIG_DIR": str(root)}),
                self.assertRaisesRegex(RuntimeError, "bootstrap peer"),
            ):
                runpy.run_path(str(SCRIPT))
            self.assertFalse((root / "garage0.toml").exists())
