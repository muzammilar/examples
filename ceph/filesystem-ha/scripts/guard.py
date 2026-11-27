"""A removed deployment must not be silently recreated over its old fixture."""

import json
import lab

path = lab.ROOT / "results/fixture.json"
if path.exists():
    data = json.loads(path.read_text())
    if (path.parent / ("down-" + data["id"] + ".json")).exists():
        raise SystemExit(
            "CephFS was removed: finish make docker-down before creating a fresh lab"
        )
