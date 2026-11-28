"""Teardown must fail before mutation when acknowledgement/ownership checks fail."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class TeardownSafety(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.command = Mock(
            side_effect=lambda *args: SimpleNamespace(
                stdout=(
                    ""
                    if any(str(arg).startswith("clickstack-") for arg in args)
                    else "owned-id"
                )
            )
        )
        self.readback = Mock()
        self.fence = Mock()
        self.owned = Mock(
            return_value={"Id": "owned-id", "State": {"Running": False}, "Mounts": []}
        )
        control = SimpleNamespace(
            ROOT=self.root,
            PROJECT="isolated-test",
            SERVICE="openobserve-sync",
            DOCKER=["never-docker"],
            command=self.command,
            owned=self.owned,
            save=Mock(),
            retired=lambda _: False,
            active_backend=Mock(),
        )
        experiments = SimpleNamespace(
            fence=self.fence,
            readback=self.readback,
            running_actors=Mock(return_value=[]),
        )
        spec = importlib.util.spec_from_file_location(
            "tested_lifecycle",
            Path(__file__).resolve().parents[1] / "scripts/lifecycle.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, control=control, experiments=experiments):
            spec.loader.exec_module(self.module)

    def tearDown(self):
        self.temporary.cleanup()

    def case(self, fenced=True):
        case = self.root / "results/cases/input"
        (case / "openobserve/metrics").mkdir(parents=True)
        (case / "case.json").write_text(json.dumps({"service": "openobserve-sync"}))
        (case / "openobserve/metrics/requests.jsonl").write_text("{}\n")
        if fenced:
            (case / "openobserve/fenced.json").write_text("{}")
        return case

    def test_failed_oracle_cannot_stop_or_delete_backend(self):
        self.case()
        self.readback.side_effect = RuntimeError("missing acknowledged span")
        with self.assertRaisesRegex(RuntimeError, "missing acknowledged"):
            self.module.remove()
        self.command.assert_not_called()

    def test_interrupted_publication_is_not_implicitly_fenced(self):
        self.case(fenced=False)
        with self.assertRaisesRegex(RuntimeError, "Unfenced"):
            self.module.remove()
        self.fence.assert_not_called()
        self.command.assert_not_called()

    def test_missing_receipt_blocks_destructive_compose_command(self):
        with self.assertRaisesRegex(RuntimeError, "No verified teardown receipt"):
            self.module.destroy()
        self.assertFalse(
            any("down" in call.args for call in self.command.call_args_list)
        )

    def test_missing_container_does_not_authorize_deleting_preserved_volume(self):
        self.case()
        self.command.side_effect = None
        self.command.return_value = SimpleNamespace(stdout="")
        with patch.object(
            self.module.subprocess, "run", return_value=SimpleNamespace(returncode=1)
        ):
            with self.assertRaisesRegex(RuntimeError, "Preserved cases lack"):
                self.module.destroy()
        self.assertFalse(
            any("down" in call.args for call in self.command.call_args_list)
        )

    def test_other_platform_state_blocks_project_destruction(self):
        self.command.side_effect = None
        self.command.return_value = SimpleNamespace(stdout="other-platform")
        with self.assertRaisesRegex(RuntimeError, "ClickStack state requires"):
            self.module.destroy()
        self.assertFalse(
            any("down" in call.args for call in self.command.call_args_list)
        )

    def test_preserving_stop_never_issues_destruction_receipt(self):
        self.module.stop_preserving()
        saved = self.module.save.call_args.args[1]
        self.assertIs(saved["verified_for_destruction"], False)
        self.assertFalse(
            any("down" in call.args for call in self.command.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
