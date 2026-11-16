"""An empty native volume server is valid; an empty whole cluster is not."""

import io
import json
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent / "s3-workload/seaweed_audit.py"


class EmptyNodes(unittest.TestCase):
    def exercise(self, statuses):
        def response(url, **kwargs):
            node = int(url.split("volume")[1].split(":")[0])
            return io.BytesIO(json.dumps(statuses[node]).encode())

        with (
            patch("urllib.request.urlopen", side_effect=response),
            patch.object(sys, "argv", [str(SCRIPT), "snapshot"]),
            patch("pathlib.Path.write_text") as write,
        ):
            runpy.run_path(str(SCRIPT), run_name="__main__")
            return json.loads(write.call_args.args[0])

    def test_null_on_empty_member_preserves_existing_volume(self):
        statuses = [{"Volumes": None} for _ in range(9)]
        for i in [1, 3, 6]:
            statuses[i] = {"Volumes": [{"Id": 7, "FileCount": 2, "Size": 2048}]}
        self.assertEqual(self.exercise(statuses), ["7"])

    def test_all_members_empty_must_fail(self):
        with self.assertRaisesRegex(RuntimeError, "no native volumes"):
            self.exercise([{"Volumes": None} for _ in range(9)])
