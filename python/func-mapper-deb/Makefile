
# shell variables
SHELL := /bin/bash  # use this instead of /bin/sh
DATE := $(shell date -u +"%Y-%m-%d")
PWD := $(shell pwd)
MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
MKFILE_DIR := $(dir $(MKFILE_PATH))
# only needed for `make deb` (i.e. inside the build container)
DISTRO := $(shell lsb_release -cs 2>/dev/null)

# Development only variables
DEV_VENV_PATH := ./.venv
# interpreter used to create the dev virtualenv (the package requires Python >=3.13).
# Override with e.g. `make dev-setup SYSTEM_PYTHON=/usr/bin/python3.13`; without a local 3.13,
# `uv python install 3.13` provides a `python3.13` in ~/.local/bin (see README.md)
SYSTEM_PYTHON ?= python3.13

# Get the build/release version
ifdef PKG_VERSION
PKG_VERSION := $(PKG_VERSION)
else
PKG_VERSION := 0.0.1-0
endif

# directory containing the final debian file (this can be relative or absolute, default is '..')
DEB_OUT_DIR = dist

# Package Info
PKG_NAME = funkpkg
# note: this must match debian/funkpkg.minsysusers and debian/funkpkg.funcmapper.service
PKG_USER = funkuser

# Install locations
PATH_PKG_LOGS = /var/log/funcmapper
PKG_SERVICE = funcmapper
PY_INSTALL_SUFFIX = funcmapper

# build container
BUILDER_IMAGE = funkpkg-builder

# docker compose monitoring stack (funk + prometheus + grafana), see docker-compose.yml
COMPOSE = docker compose --file ${MKFILE_DIR}docker-compose.yml
# images built (not pulled) by docker-compose.yml; `make docker-down` removes only these
COMPOSE_BUILT_IMAGES = funkpkg-run:local
PROMETHEUS_PORT ?= 19118
GRAFANA_PORT ?= 13018
export PROMETHEUS_PORT
export GRAFANA_PORT

# exports (used by debian/rules and the #ENV.*# substitutions in debian/funkpkg.postinst)
export DEB_OUT_DIR
export PATH_PKG_LOGS
export PKG_NAME
export PKG_SERVICE
export PKG_USER
export PKG_VERSION
export PY_INSTALL_SUFFIX

# Makefile
.PHONY: all test test-all clean run dev-setup wheel wheel-clean wheel-build wheel-inspect \
	bench deb deb-clean deb-clean-changelog deb-changelog-create deb-build deb-inspect buildcontainer \
	docker-up docker-down docker-logs
all: clean test

clean: deb-clean wheel-clean
	-rm -rf build/ *.egg-info .pytest_cache .benchmarks

# setup a development virtualenv
dev-setup:
	@command -v ${SYSTEM_PYTHON} >/dev/null || { echo "${SYSTEM_PYTHON} not found: install Python 3.13 (e.g. 'uv python install 3.13') or set SYSTEM_PYTHON" >&2; exit 1; }
# the venv is created from the resolved interpreter path: a venv made through a symlink (such as
# uv's ~/.local/bin/python3.13) records the symlink's directory as its home and can't find the stdlib
	$(realpath $(shell command -v ${SYSTEM_PYTHON})) -m venv ${DEV_VENV_PATH}
	${DEV_VENV_PATH}/bin/pip3 install --upgrade pip
	${DEV_VENV_PATH}/bin/pip3 install -r ${MKFILE_DIR}/requirements.txt
	${DEV_VENV_PATH}/bin/pip3 install -r ${MKFILE_DIR}/test_requirements.txt

# pytest markers (see pytest.ini) keep the unit tests and the benchmarks apart:
# `make test` deselects `benchmark`, `make bench` selects only `benchmark`.
# Override with e.g. `make test MARKERS=unit` or `make bench BENCH_ARGS=--benchmark-min-rounds=50`.
MARKERS ?= not benchmark
BENCH_ARGS ?=

# test the code (run `make dev-setup` first)
test:
	${DEV_VENV_PATH}/bin/python3 -m pytest tests benchmarks -m "${MARKERS}"

# run the benchmarks (run `make dev-setup` first)
bench:
	${DEV_VENV_PATH}/bin/python3 -m pytest tests benchmarks -m benchmark --benchmark-only ${BENCH_ARGS} --benchmark-group-by=group --benchmark-columns=min,mean,median,ops,rounds

# run the unit tests, then the benchmarks
test-all: test bench

# run the program once from the source tree (run `make dev-setup` first, it installs prometheus_client)
RUN_ARGS ?=
run:
	${DEV_VENV_PATH}/bin/python3 dev.py ${RUN_ARGS}

# wheel (run `make dev-setup` first, it installs `setuptools` and `wheel`)
wheel: wheel-clean wheel-build wheel-inspect

wheel-clean:
	-rm -rf dist/*.whl dist/package.zip

wheel-build:
	${DEV_VENV_PATH}/bin/pip3 wheel --quiet --no-deps --no-build-isolation --wheel-dir dist .

wheel-inspect:
	@echo WHEELCONTENTS:START
	cp -v dist/${PKG_NAME}*.whl dist/package.zip
	unzip -l dist/package.zip
	@echo WHEELCONTENTS:END


# debian building (run inside the build container, see `make buildcontainer`)
# note: we create a deb changelog here because it's an example. Ideally, the changelog file should be only incremented with `dch -i`
deb: deb-clean deb-changelog-create deb-build deb-inspect

deb-clean:
	-rm -fv ${DEB_OUT_DIR}/*.deb ${DEB_OUT_DIR}/*.buildinfo ${DEB_OUT_DIR}/*.changes
	-rm -rf debian/${PKG_NAME} debian/.debhelper/ debian/debhelper-build-stamp debian/files
	-rm -f debian/*.substvars debian/*.debhelper.log debian/*.debhelper

deb-clean-changelog:
	-rm -f debian/changelog

deb-changelog-create: deb-clean-changelog
	dch --create --package ${PKG_NAME} -D ${DISTRO} -v ${PKG_VERSION} "Release: ${PKG_VERSION}"

deb-build:
# check build deps
	dpkg-checkbuilddeps
# build binary package only (-b), unsigned (-us -uc)
# the .deb is written to ${DEB_OUT_DIR} by debian/rules; also keep the .buildinfo/.changes there (default is '..')
	$(eval DEB_ARCH := $(shell dpkg-architecture -qDEB_HOST_ARCH))
	dpkg-buildpackage -b -us -uc \
		--buildinfo-option="-u${DEB_OUT_DIR}" --changes-option="-u${DEB_OUT_DIR}" \
		--buildinfo-file="${DEB_OUT_DIR}/${PKG_NAME}_${PKG_VERSION}_${DEB_ARCH}.buildinfo" \
		--changes-file="${DEB_OUT_DIR}/${PKG_NAME}_${PKG_VERSION}_${DEB_ARCH}.changes"

deb-inspect:
	dpkg --info ${DEB_OUT_DIR}/${PKG_NAME}_*.deb
	dpkg --contents ${DEB_OUT_DIR}/${PKG_NAME}_*.deb

# build the debian package inside a container that has all build dependencies
buildcontainer:
	docker build -t ${BUILDER_IMAGE} .
	docker run --rm --volume "${PWD}:/pkgbuilddir" --workdir="/pkgbuilddir" ${BUILDER_IMAGE} make deb

# start the monitoring stack: funk (metrics on :8000 inside the network), prometheus and grafana
docker-up:
	${COMPOSE} up --build --detach
	@echo "Prometheus: http://localhost:${PROMETHEUS_PORT}"
	@echo "Grafana:    http://localhost:${GRAFANA_PORT}/d/funcmapper (anonymous admin)"

# remove this project's containers, networks, volumes and orphans, and only the images it builds
# (the pulled prometheus/grafana/python images are kept)
docker-down:
	${COMPOSE} down --volumes --remove-orphans
	-docker image rm ${COMPOSE_BUILT_IMAGES}

docker-logs:
	${COMPOSE} logs --follow --tail=100
