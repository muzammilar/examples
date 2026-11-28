"""The read-only unused-volume exception must not accept foreign storage."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lab import unused_prometheus_volume


class Provenance(unittest.TestCase):
    def test_only_declared_unused_image_volume_is_recognized(self):
        owner = {"Config": {"Cmd": ["--storage.tsdb.path=/data"]}}
        image = {"Config": {"Volumes": {"/prometheus": {}}}}
        mount = {"Destination": "/prometheus"}
        for labels in [{}, {"com.docker.volume.anonymous": ""}]:
            self.assertTrue(
                unused_prometheus_volume("prometheus", owner, mount, labels, image)
            )
        self.assertFalse(unused_prometheus_volume("loki", owner, mount, {}, image))
        self.assertFalse(
            unused_prometheus_volume(
                "prometheus", owner, mount, {"owner": "foreign"}, image
            )
        )
        self.assertFalse(
            unused_prometheus_volume(
                "prometheus", owner, {"Destination": "/data"}, {}, image
            )
        )
        self.assertFalse(
            unused_prometheus_volume("prometheus", owner, mount, {}, {"Config": {}})
        )
        self.assertFalse(
            unused_prometheus_volume(
                "prometheus",
                {"Config": {"Cmd": ["--storage.tsdb.path=/prometheus"]}},
                mount,
                {},
                image,
            )
        )


if __name__ == "__main__":
    unittest.main()
