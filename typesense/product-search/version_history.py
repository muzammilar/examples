"""Validate immutable version intents/outcomes; mutable rows retain their highest ACK."""

import hashlib
import json
import math
from pathlib import Path


def records(path, *, live=False):
    path = Path(path)
    if not path.exists():
        return []
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        if not live:
            raise RuntimeError("incomplete durable journal: " + str(path))
        data = data[: data.rfind(b"\n") + 1]
    return [json.loads(line) for line in data.split(b"\n") if line]


class History:
    def __init__(self, prior):
        self.ack = dict(prior["ack_versions"])
        self.times = dict(prior["ack_times"])
        self.attempted = {
            key: set(values) for key, values in prior["attempted_versions"].items()
        }
        self.pending = {}
        self.operations = 0
        self.acknowledgements = 0
        self.last_version = max(
            (v for values in self.attempted.values() for v in values), default=0
        )
        for key, version in self.ack.items():
            if (
                version not in self.attempted.get(key, ())
                or self.times.get(key, 0) <= 0
            ):
                raise RuntimeError("historical ACK lacks an intent/timestamp")

    def append(self, event):
        stage = event["stage"]
        if stage not in {"attempt", "ack", "ambiguous", "rejected"}:
            raise RuntimeError("unknown writer event")
        if not math.isfinite(event["time"]) or event["time"] <= 0 or not event["rows"]:
            raise RuntimeError("invalid writer timestamp/empty batch")
        rows = event["rows"]
        if stage == "attempt":
            versions = {row["version"] for row in rows}
            if len(versions) != 1:
                raise RuntimeError("intent batch has multiple versions")
            version = versions.pop()
            if type(version) is not int or not self.last_version < version < 1000000000:
                raise RuntimeError("intent version is not globally increasing")
            self.last_version = version
        for row in rows:
            key, version = row["id"], row["version"]
            if (
                type(key) is not str
                or not key.isascii()
                or not key.isdecimal()
                or str(int(key)) != key
            ):
                raise RuntimeError("noncanonical product identity")
            if type(version) is not int or version <= 0:
                raise RuntimeError("invalid inventory version")
            pair = (key, version)
            if stage == "attempt":
                if version in self.attempted.get(key, ()):
                    raise RuntimeError("duplicate version intent")
                self.attempted.setdefault(key, set()).add(version)
                self.pending[pair] = row
                self.operations += 1
            else:
                if pair not in self.pending or row != self.pending[pair]:
                    raise RuntimeError("outcome lacks a unique matching intent")
                self.pending.pop(pair)
                if stage == "ack":
                    if version <= self.ack.get(key, 0):
                        raise RuntimeError("ACK version regressed")
                    self.ack[key] = version
                    self.times[key] = event["time"]
                    self.acknowledgements += 1

    def value(self):
        return {
            "ack_versions": self.ack,
            "ack_times": self.times,
            "attempted_versions": {
                key: sorted(values) for key, values in self.attempted.items()
            },
        }


def empty():
    return {"ack_versions": {}, "ack_times": {}, "attempted_versions": {}}


def load_current(root, *, live=False):
    root = Path(root)
    history = History(json.loads((root / "history.json").read_text()))
    for event in records(root / "writer.jsonl", live=live):
        history.append(event)
    return history


class EventTail:
    """Read complete appended LF records once; retain any unfinished final line."""

    def __init__(self, path):
        self.path = Path(path)
        self.file = None
        self.pending = b""

    def read(self):
        if self.file is None:
            try:
                self.file = self.path.open("rb")
            except FileNotFoundError:
                return []
        lines = (self.pending + self.file.read()).split(b"\n")
        self.pending = lines.pop()
        return [json.loads(line) for line in lines]

    def close(self):
        if self.file is not None:
            self.file.close()


class Follower:
    """Validate the newly appended records against the retained historical witness."""

    def __init__(self, root):
        self.root = Path(root)
        self.history = History(json.loads((self.root / "history.json").read_text()))
        self.tail = EventTail(self.root / "writer.jsonl")

    def refresh(self):
        for event in self.tail.read():
            self.history.append(event)
        return self.history

    def close(self):
        self.tail.close()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
