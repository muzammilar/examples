"""Generate a minimal single-copy stack from the tested pinned service definitions."""

import json
import os
from pathlib import Path
import subprocess

root = Path.cwd()
base = root / "definitions"
generated = root / "config" / "generated"
generated.mkdir(parents=True, exist_ok=True)
(root / "results" / "generated").mkdir(parents=True, exist_ok=True)
project = "neon-single"
env = {**os.environ, "COMPOSE_PROJECT_NAME": project}
config = json.loads(
    subprocess.check_output(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "--profile",
            "workload",
            "config",
            "--format",
            "json",
        ],
        cwd=base,
        env=env,
        text=True,
    )
)
selected = ("broker", "pageserver", "sk0", "compute", "pg0", "runner")
config["services"] = {name: config["services"][name] for name in selected}
template = json.loads((base / "config" / "compute-template.json").read_text())
for value in template["spec"]["cluster"]["settings"]:
    if value["name"] == "neon.safekeepers":
        value["value"] = "sk0:5454"
(generated / "compute-template.json").write_text(json.dumps(template, indent=2) + "\n")
page_config = (base / "config" / "pageserver.toml").read_text().splitlines()
page_config = [
    'remote_storage = { local_path="/data/remote" }'
    if line.startswith("remote_storage")
    else line
    for line in page_config
]
(generated / "pageserver.toml").write_text("\n".join(page_config) + "\n")
for name, service in config["services"].items():
    for mount in service.get("volumes", []):
        if mount["type"] != "bind":
            continue
        target = mount["target"]
        if target == "/results":
            mount["source"] = str(root / "results")
        elif target == "/config/config.json":
            mount["source"] = str(root / "results" / "generated" / "compute.json")
        elif target in ("/config/pageserver.toml", "/config/compute-template.json"):
            mount["source"] = str(generated / Path(target).name)
    if name == "sk0":
        service["command"] = [
            arg for arg in service["command"] if not arg.startswith("--remote-storage=")
        ]
    if name == "pg0":
        service["volumes"] = [m for m in service["volumes"] if m["type"] == "volume"]
        service["command"] = ["postgres", "-c", "synchronous_commit=on"]
    if name == "runner":
        service["environment"]["PG_REPLICAS"] = "0"
        service["environment"]["ROWS"] = os.environ.get("ROWS", "1000000")
used = {
    m["source"]
    for s in config["services"].values()
    for m in s.get("volumes", [])
    if m["type"] == "volume"
}
config["volumes"] = {
    key: value for key, value in config["volumes"].items() if key in used
}
(root / "docker-compose.json").write_text(json.dumps(config, indent=2) + "\n")
