import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from topology import check_doris


class TabletTests(unittest.TestCase):
    def fixture(self):
        frontends = [
            dict(Alive="true", Role="FOLLOWER", IsMaster="true" if i == 0 else "false")
            for i in range(3)
        ]
        tablets = [
            dict(TabletId=str(t), BackendId=str(b), Version="2", State="NORMAL")
            for t in range(3)
            for b in range(3)
        ]
        return {
            "SHOW FRONTENDS": frontends,
            "SHOW BACKENDS": [dict(Alive="true") for _ in range(3)],
            "SHOW TABLETS FROM pipeline.orders": tablets,
            "SHOW TABLETS FROM pipeline.regions": tablets.copy(),
        }

    def check(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.log"
            path.write_text(
                "Container oracle Creating\n"
                + "\n".join(json.dumps(dict(query=k, rows=v)) for k, v in data.items())
            )
            return check_doris(path)

    def test_three_current_copies(self):
        self.assertEqual(self.check(self.fixture())["current_replicas"], 18)

    def test_stale_copy_is_not_a_pass(self):
        data = self.fixture()
        data["SHOW TABLETS FROM pipeline.orders"][0]["Version"] = "1"
        with self.assertRaises(ValueError):
            self.check(data)

    def test_duplicate_backend_is_not_three_copies(self):
        data = self.fixture()
        data["SHOW TABLETS FROM pipeline.orders"][0]["BackendId"] = "1"
        with self.assertRaises(ValueError):
            self.check(data)
