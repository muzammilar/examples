# Function Mapper (Debian Package)

A small Python program (`funcmapper`) that maps function *names* (strings) to
Python functions and calls them, packaged as a Debian package with:

* [dh-virtualenv](https://github.com/spotify/dh-virtualenv): ships the code in a self-contained virtualenv at `/opt/venv/funcmapper`
* [dh-sysuser](https://salsa.debian.org/debian/dh-sysuser): creates the `funkuser` system user on install (from a sysusers.d file)
* `dh_installsystemd`: installs and enables the `funcmapper.service` unit
* `dh_installlogrotate`: rotates `/var/log/funcmapper/*.log`
* [prometheus_client](https://github.com/prometheus/client_python): optional `/metrics` endpoint (`--metrics-port`),
  plus a docker compose stack with Prometheus and a pre-provisioned Grafana dashboard (see [Monitoring](#monitoring))

## Versions

| component | version |
|-----------|---------|
| Python | 3.13 (`python_requires=">=3.13"`; trixie's system `python3` is 3.13.5) |
| Build container (`Dockerfile`) | `debian:trixie` (Debian 13), debhelper 13.24.2 (compat 13, the recommended level; 14 is still experimental), dh-virtualenv 1.2.2-1.7, dh-sysuser 1.6.0 |
| Runtime image (`Dockerfile.run`) | `python:3.13-slim` |
| Runtime dependency (`requirements.txt`) | prometheus_client 0.26.0 (`~=0.26.0`) |
| Dev dependencies (`test_requirements.txt`) | pytest 9.1.1, pytest-benchmark 5.3.0, setuptools 84.0.0, wheel 0.48.0 |
| Monitoring stack (`docker-compose.yml`) | prom/prometheus v3.15.0, grafana/grafana 13.2.2 |

## Layout

```
funcmapper/
  functions.py        # the functions to be mapped (rails, cylinders, oranges)
  maps.py             # name -> function map, and `call(name, *args, **kwargs)`
  funk.py             # `funk` console script (entry point of the service)
  metrics.py          # Prometheus metrics (prometheus_client) and the /metrics HTTP server
  logger/             # file logging via WatchedFileHandler (logrotate friendly)
tests/                # pytest unit tests (functions, maps, CLI, logger)
benchmarks/           # pytest-benchmark benchmarks (`make bench`)
dev.py                # run from the source tree without installing
setup.py              # python packaging (used by dh-virtualenv)
Dockerfile            # debian:trixie build container with all build deps (`make buildcontainer`)
Dockerfile.run        # python:3.13-slim runtime image for the monitoring demo (`make docker-up`)
docker-compose.yml    # funk + Prometheus + Grafana
prometheus/prometheus.yml                  # scrapes funk:8000 every 5s
grafana/provisioning/datasources/prometheus.yml
grafana/provisioning/dashboards/dashboards.yml
grafana/provisioning/dashboards/funcmapper.json  # the "funcmapper" dashboard
debian/
  control             # package metadata and build dependencies
  rules               # dh with sysuser + python-virtualenv
  funkpkg.minsysusers # system user to create (sysusers.d format, installed as /usr/lib/sysusers.d/funkpkg.conf)
  funkpkg.postinst    # creates the log dir (uses #ENV.*# values exported by the Makefile)
  funkpkg.funcmapper.service    # installed as funcmapper.service
  funkpkg.funcmapper.logrotate  # installed as /etc/logrotate.d/funcmapper
```

## Local development

`make dev-setup` creates `./.venv` with `python3.13` (override with `SYSTEM_PYTHON`, e.g.
`make dev-setup SYSTEM_PYTHON=/usr/bin/python3.13`). If the host has no Python 3.13, install one with
[uv](https://docs.astral.sh/uv/); it puts a `python3.13` into `~/.local/bin`, which must be on `PATH`:

```sh
uv python install 3.13
```

`make run`, `make test`, `make bench`, `make test-all` and `make wheel` use `./.venv`, so run
`make dev-setup` first.

```sh
# create a virtualenv (./.venv) with the runtime (prometheus_client) and dev dependencies
make dev-setup

# run once from the source tree (funk --interval 0)
make run
# run every 5 seconds, also log to a file and serve metrics on http://localhost:8000/metrics
make run RUN_ARGS="--interval 5 --log-file /tmp/funkmapper.log --metrics-port 8000"

# run the unit tests (benchmarks are excluded)
make test

# run the benchmarks: each mapped function called directly vs. through `maps.call`
make bench
# unit tests, then benchmarks
make test-all
```

Unit tests and benchmarks are separated with pytest markers (registered in `pytest.ini`): tests are
marked `unit` and benchmarks `benchmark`. `make test` runs `-m "not benchmark"` and `make bench` runs
`-m benchmark`, so neither target runs the other's tests. Pick a different marker expression with
`make test MARKERS=unit`, and pass extra pytest-benchmark flags with
`make bench BENCH_ARGS=--benchmark-min-rounds=50`.

`pytest`, `pytest-benchmark`, `setuptools` and `wheel` are development dependencies only (`test_requirements.txt`).
They are not part of the Debian package, which installs only `requirements.txt` into its virtualenv.
`make bench` also has a `test_tracked_call` case: `maps.call` wrapped in the metrics, which is what
`funk` runs for every call.

Example `make bench` output (Apple Silicon, Python 3.13, median): calling through
`maps.call` adds roughly 30-140 ns (the dictionary lookup plus one extra call) compared to calling the
function directly, and the metrics (`test_tracked_call`) add about 2 µs per call:

| function  | direct | `maps.call` | tracked |
|-----------|-------:|------------:|--------:|
| rails     |  46 ns |       80 ns | 2.0 µs |
| cylinders | 108 ns |      250 ns | 2.2 µs |
| oranges   | 371 ns |      500 ns | 2.5 µs |

To build a wheel into `dist/` (and list its contents): `make wheel`.

## Building the Debian package

```sh
# build the debian:trixie container (it contains all build dependencies) and run `make deb` inside it
make buildcontainer

# or, on a Debian 13 (trixie) machine with the build dependencies from debian/control installed
make deb

# the package (plus .buildinfo/.changes) is written to dist/
dpkg --info dist/funkpkg_*.deb
dpkg --contents dist/funkpkg_*.deb
```

`prometheus_client` is the one runtime dependency (`requirements.txt`, pinned with `~=`, and read by
`setup.py` for `install_requires`). dh-virtualenv installs it into the package's virtualenv, so the deb
bundles it and the target machine doesn't need `python3-prometheus-client` or network access:

```sh
dpkg --contents dist/funkpkg_*.deb | grep 'site-packages/prometheus_client'
# ./opt/venv/funcmapper/lib/python3.13/site-packages/prometheus_client/
# ./opt/venv/funcmapper/lib/python3.13/site-packages/prometheus_client-0.26.0.dist-info/
```

The virtualenv links to the target's `/usr/bin/python3`, so the package depends on
`python3 (>= 3.13), python3 (<< 3.14)` (plus `sysuser-helper` from dh-sysuser). The changelog
distribution is taken from `lsb_release -cs` (`trixie` in the build container).

The version defaults to `0.0.1-0` (the Python package version, and the `funcmapper_build_info` label, is its
PEP 440 form `0.0.1.post0`) and can be set with `PKG_VERSION`, e.g. `make buildcontainer PKG_VERSION=0.0.2-1`.
The `debian/changelog` is generated by `make deb` (it's an example; normally it would be maintained with `dch -i`).

## Installing

```sh
# install the deb on a Debian 13 (trixie) machine (the architecture depends on the build machine, e.g. amd64 or arm64)
apt install -y ./dist/funkpkg_0.0.1-0_amd64.deb

systemctl status funcmapper
tail -f /var/log/funcmapper/funcmapper.log
```

The service runs `funk --interval 60 --log-file /var/log/funcmapper/funcmapper.log` as `funkuser`.
Results also go to stdout (the journal).

`funk` flags:

| flag | default | description |
|------|---------|-------------|
| `--interval SECONDS` | `0` | seconds between runs; `0` runs once and exits |
| `--log-file PATH` | none | also write results to this file |
| `--log-level LEVEL` | `info` | `debug`, `info`, `warning` or `error` |
| `--metrics-port PORT` | `0` | serve Prometheus metrics on `PORT` at `/metrics`; `0` disables it |

Note: `dh-sysuser` does not delete the `funkuser` user on `apt purge`.

The systemd unit leaves metrics **off** by default: `--metrics-port` binds on all interfaces with no
authentication, so a package shouldn't open a port just by being installed. To turn them on, add a
drop-in:

```sh
systemctl edit funcmapper
# [Service]
# ExecStart=
# ExecStart=/opt/venv/funcmapper/bin/funk --interval 60 --log-file /var/log/funcmapper/funcmapper.log --metrics-port 9118
curl -s localhost:9118/metrics | grep ^funcmapper_
```

## Monitoring

`funk --metrics-port PORT` serves Prometheus metrics at `http://HOST:PORT/metrics`
(`0`, the default, disables it). All metrics are prefixed with `funcmapper_`:

| metric | type | labels | description |
|--------|------|--------|-------------|
| `funcmapper_calls_total` | counter | `function` | mapped function calls |
| `funcmapper_errors_total` | counter | `function` | calls that raised (the exception is still raised) |
| `funcmapper_call_duration_seconds` | histogram | `function` | call duration (buckets from 100ns to 1s) |
| `funcmapper_loop_iterations_total` | counter | | completed runs of the main loop |
| `funcmapper_last_run_timestamp_seconds` | gauge | | unix time of the last completed run |
| `funcmapper_build_info` | info | `version` | installed `funkpkg` version (PEP 440 form, e.g. `0.0.1-0` shows as `0.0.1.post0`) |

The per-function series are created at start-up with value 0, so `rate()` queries return data before the
first error. The instrumentation lives in `funk.run` (`maps.call` stays a plain lookup), and the metrics
are grouped on a `funcmapper.metrics.Metrics` object bound to a `CollectorRegistry`. `funk` uses the
default registry; the tests use their own `Metrics(CollectorRegistry())`, so they don't share state.

### Docker compose stack

```sh
make docker-up     # builds Dockerfile.run and starts funk, Prometheus and Grafana
make docker-logs   # follow the logs
make docker-down   # remove the containers, network, volumes and the built funkpkg-run:local image
```

* Prometheus: http://localhost:19118 (scrapes `funk:8000` every 5s; the app port is not published on the host)
* Grafana: http://localhost:13018/d/funcmapper (anonymous Admin, no login)

The host ports can be changed with `make docker-up PROMETHEUS_PORT=19119 GRAFANA_PORT=13019`.
The `funk` container runs `funk --interval 2 --metrics-port 8000`, pip-installed into `python:3.13-slim`
(it doesn't use the deb, so it doesn't need the build container). `make docker-down` keeps the pulled
`python`, `prom/prometheus` and `grafana/grafana` images.

Grafana is provisioned with a `Prometheus` datasource (uid `prometheus`) and the `funcmapper` dashboard
(refresh 5s, last 15 minutes):

* Time since last run, loop iterations, error rate and version (stat/table row)
* Call rate by function
* Error rate by function
* Call duration p50 / p95 / p99 by function (`histogram_quantile` over `funcmapper_call_duration_seconds_bucket`)
* Loop iterations per second

## Note for Windows Docker users
Please make sure that you are using `LF` line ending and not `CRLF` for *all* files in `debian/` directory, otherwise, you will run into errors.

Example error (or `postinst` failures):
```
: No such file or directory
cc      -o .o
cc: fatal error: no input files
```
