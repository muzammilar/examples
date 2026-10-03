# examples
Example Codes in different languages and technologies, for fun.

## Dev Shell

`flake.nix` at the root provides the tools the examples' Makefiles use: `go` (with `gopls`, `golangci-lint`,
`gotools`), `kind`, `kubectl`, `helm`, `k9s`, `valkey-cli`, `jq`, `yq`, `envsubst`, `ansi2txt`. With [direnv](https://direnv.net/),
run `direnv allow` once in the repo root; or run `nix develop`. The shell adds `~/go/bin` to `PATH` and loads a
root `.env` (gitignored) if present. Docker is not included: use Docker Desktop, or Docker Engine on Linux.

## Language Examples

These directories were merged from standalone repos (now archived) using `git subtree`, with full history preserved.

| Directory | Former Repo |
|-----------|-------------|
| [`cpp/`](cpp) | [muzammilar/examples-cpp](https://github.com/muzammilar/examples-cpp) |
| [`erlang/`](erlang) | N/A |
| [`go/`](go) | [muzammilar/examples-go](https://github.com/muzammilar/examples-go) |
| [`js/`](js) | [muzammilar/examples-js](https://github.com/muzammilar/examples-js) |
| [`python/`](python) | [muzammilar/examples-python](https://github.com/muzammilar/examples-python) |
| [`rust/`](rust) | [muzammilar/examples-rust](https://github.com/muzammilar/examples-rust) |

Some Go modules still declare `github.com/muzammilar/examples-go/...` module paths; they build fine from their new location.

## External Projects (Git Submodules)

See [git submodule examples](go/ext/README.md).

#### Adding/Updating a submodule

```sh

git submodule add https://github.com/muzammilar/<repo>.git <repo>

```
