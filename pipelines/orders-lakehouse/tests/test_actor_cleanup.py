"""A startup failure must not leave the reader or source running unattended."""

from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recovery


class StartupCleanup(TestCase):
    def test_reader_failure_stops_reader(self):
        reader = Mock()
        with patch.object(
            recovery.p,
            "settled",
            return_value={"metadata": "s3://warehouse/pin.metadata.json"},
        ), patch.object(recovery.p, "Background", return_value=reader), patch.object(
            recovery.p, "wait", side_effect=RuntimeError("reader unavailable")
        ):
            with self.assertRaisesRegex(RuntimeError, "reader unavailable"):
                recovery.begin(
                    Path("."),
                    {"phases": ["baseline"], "rows": 10},
                    "live",
                    ["baseline"],
                )
        reader.finish.assert_called_once_with(stop=True)

    def test_source_start_failure_joins_both_actors(self):
        reader, writer = Mock(), Mock()
        with patch.object(
            recovery.p,
            "settled",
            return_value={"metadata": "s3://warehouse/pin.metadata.json"},
        ), patch.object(
            recovery.p, "Background", side_effect=[reader, writer]
        ), patch.object(
            recovery.p, "wait", side_effect=[True, RuntimeError("source unavailable")]
        ), patch.object(
            recovery.lab, "save"
        ):
            with self.assertRaisesRegex(RuntimeError, "source unavailable"):
                recovery.begin(
                    Path("."),
                    {"phases": ["baseline"], "rows": 10},
                    "live",
                    ["baseline"],
                )
        writer.finish.assert_called_once_with()
        reader.finish.assert_called_once_with(stop=True)
