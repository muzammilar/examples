"""Docker CLI ID formatting must not turn the intended restart into a failure."""

from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from control import discover_survivors


class CanonicalIdentity(TestCase):
    def test_short_cli_ids_are_resolved_before_excluding_target(self):
        target = "a" * 64
        survivor = "b" * 64
        rows = {
            target[:12]: dict(Id=target, State={"StartedAt": "target-start"}),
            survivor[:12]: dict(Id=survivor, State={"StartedAt": "survivor-start"}),
        }
        with patch(
            "lab.output", return_value=target[:12] + "\n" + survivor[:12]
        ), patch("lab.owned", side_effect=rows.__getitem__):
            self.assertEqual(discover_survivors(target), {survivor: "survivor-start"})
