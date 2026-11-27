"""Run the narrow diagnosed credential-file recovery, then reconcile old ACKs."""

import time
import lab
import test as checks

data = checks.fixture()
directory = lab.ROOT / "results" / ("keyring-recovery-" + str(time.time_ns()))
directory.mkdir()
result = lab.native("recover_keyrings.py", data["fsid"])
(directory / "repair.json").write_text(result.stdout)
checks.audit(directory)
print(directory)
