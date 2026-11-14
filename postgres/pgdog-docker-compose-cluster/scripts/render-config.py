"""Render the one tunable pool setting; validate TOML before updating PgDog."""
import os
from pathlib import Path
from string import Template
import tomllib

size = int(os.environ.get("POOL_SIZE", "16"))
if not 1 <= size <= 128:
    raise SystemExit("POOL_SIZE must be between 1 and 128")
text = Template(Path("config/pgdog.toml.in").read_text()).substitute(POOL_SIZE=size)
tomllib.loads(text)
Path("config/pgdog.toml").write_text(text)
