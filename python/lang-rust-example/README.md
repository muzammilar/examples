# Readme

## Initial Setup

```sh
# Initialize Module
uv init .
uv tool install maturin
# create a python module
mkdir my_module
touch my_module/__init__.py
# create a cargo project
# cargo new something --lib
cargo init --lib
# You can either specify maturin-related components in pyproject.toml or cargo.toml, we use cargo.toml here

# Write your code, setup pyproject.toml and cargo.toml manually

```
## Running

```sh
# Either
make all
# Or
uv sync
uv tool install maturin
uv run main.py
```
