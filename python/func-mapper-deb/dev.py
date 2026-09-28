#!/usr/bin/env python3
"""
An entry point for testing funcmapper in local development setup.

Usage: python3 dev.py [--interval SECONDS] [--log-file PATH] [--log-level LEVEL] [--metrics-port PORT]
(needs prometheus_client, e.g. `make dev-setup && make run RUN_ARGS=...`)
"""

import sys

import funcmapper

# This function is mostly used for local testing of the funcmapper code
if __name__ == "__main__":
    sys.exit(funcmapper.funk.main())
