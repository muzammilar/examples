# syntax=docker/dockerfile:1
# Code-quality tools without an official image. See `make quality`.

FROM rust:1-slim-bookworm
# git fetches the RustSec advisory database. Distro packages follow the base
# image's security updates and aren't pinned.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    cargo install --locked cargo-deny
WORKDIR /src
