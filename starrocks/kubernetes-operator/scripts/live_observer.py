"""Independent baseline reader and local observation report for operational tests."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from production_observations import summarize, timestamp


class LiveObserver:
    def __init__(self, case, command, root=Path("results")):
        self.case, self.root = case, root
        self.markers = [("before", datetime.now(timezone.utc))]
        self.log = (root / (case + "-reader.log")).open("w")
        self.process = subprocess.Popen(
            command, stdout=self.log, stderr=subprocess.STDOUT
        )

    def mark(self, phase):
        self.markers.append((phase, datetime.now(timezone.utc)))

    def progress(self):
        after = self.markers[-1][1]
        deadline = time.monotonic() + 180
        path = self.root / ("reads-" + self.case + ".jsonl")
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("independent reader exited before recovery")
            events = (
                [json.loads(line) for line in path.read_text().split("\n")[:-1]]
                if path.exists()
                else []
            )
            if (
                sum(
                    event["outcome"] == "ok" and timestamp(event["at"]) > after
                    for event in events
                )
                >= 3
            ):
                return
            time.sleep(1)
        raise RuntimeError("no three successful live reads before deadline")

    def finish(self):
        # The controller has already stopped and joined the writer; the shared
        # stop marker terminates the independent reader after its current request.
        (self.root / ("stop-" + self.case)).touch()
        if self.process.wait(timeout=30) != 0:
            raise RuntimeError("reader failed")
        data = summarize(
            self.root / ("fault-" + self.case + ".jsonl"),
            self.root / ("reads-" + self.case + ".jsonl"),
            self.markers,
            datetime.now(timezone.utc),
        )
        (self.root / (self.case + "-observations.json")).write_text(
            json.dumps(data, indent=2) + "\n"
        )
        if data["contract_failures"]:
            raise RuntimeError(str(data["contract_failures"]))
        return data

    def close(self):
        (self.root / ("stop-" + self.case)).touch()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)
        self.log.close()
