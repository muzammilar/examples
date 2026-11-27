"""Save a dated native resource snapshot independently of the Docker observer."""

import json
import time
import lab


def main():
    path = lab.ROOT / "results" / ("native-resources-" + str(time.time_ns()) + ".json")
    result = lab.native("resources.py")
    path.write_text(json.dumps(json.loads(result.stdout), indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
