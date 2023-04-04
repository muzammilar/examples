
# shell variables
SHELL := /bin/bash  # use this instead of /bin/sh
DATE := $(shell date -u +"%Y-%m-%d")
PWD := $(shell pwd)
DISTRO := $(shell lsb_release -cs)

# Development only variables
DEV_VENV_PATH := ~/funcmapper/venv


# Get the build/release version
ifdef PKG_VERSION
PKG_VERSION := $(PKG_VERSION)
else
PKG_VERSION := 0.0.1-0
endif

# program name
PERIODIC_PRINTER = periodicprinter

# directory containing the final debian file (this can be relative or absolute, default is '..')
DEB_OUT_DIR = dist

# Package Info
PKG_USER = funkuser
PKG_NAME = funkpkg

# Install locations
PATH_PKG_LOGS = /var/log/funcmapper
PKG_USER = funcmapper # note: this same as debian/funkpkg.sysuser
PKG_SERVICE = funcmapper
PY_INSTALL_SUFFIX = funcmapper

# exports
export DEB_OUT_DIR
export PATH_PKG_LOGS
export PKG_SERVICE
export PKG_USER
export PY_INSTALL_SUFFIX

# Makefile
.PHONY: all test clean deb-clean format lint deb install dev-setup
all: clean ${PERIODIC_PRINTER}

clean: deb-clean wheel-clean
	-rm -rf .tox

# setup a development virtualenv
dev-setup:
# setup a virtualenv
	python3 -m venv ${DEV_VENV_PATH}
	${DEV_VENV_PATH}/bin/pip3 install -r ${MKFILE_DIR}/requirements.txt
	${DEV_VENV_PATH}/bin/pip3 install -r ${MKFILE_DIR}/test_requirements.txt

# test the code
test:
	tox --recreate

# wheel
wheel: wheel-clean wheel-build wheel-inspect

wheel-clean:
	-rm -rf dist/*.whl

wheel-build:
	python3 setup.py -q bdist_wheel

wheel-inspect:
	echo WHEELCONTENTS:START
	cp -v dist/${PKG_NAME}*.whl dist/package.zip
	unzip -l dist/package.zip
	echo WHEELCONTENTS:END


# debian building
# note: we create a deb changelog here because it's an example. Ideally, the changelog file should be only incremented with `dch -i`
deb: deb-clean deb-changelog-create deb-build

deb-clean:
	-rm -f dist/*.deb
	-rm -fv dist/*
	-rm -rf debian/${PKG_NAME}
	-rm -rf debian/.debhelper/

deb-clean-changelog:
	-rm -f debian/changelog

deb-changelog-create: deb-clean-changelog
	dch --create --package ${PKG_NAME} -D ${DISTRO} -v ${PKG_VERSION} "Release: ${PKG_VERSION}"

deb-build:
# check build deps
	dpkg-checkbuilddeps
# build
# --buildinfo-option is passed to dpkg-genbuildinfo
# --changes-option is passed to dpkg-genchanges
#	dpkg-buildpackage --unsigned-source --unsigned-changes --buildinfo-option="-u${DEB_OUT_DIR}" --changes-option="-u${DEB_OUT_DIR}"
	dpkg-buildpackage --unsigned-source --unsigned-changes

buildcontainer:
	docker build -t ppbuilder .
	docker run --rm --volume "${PWD}:/pkgbuilddir" --workdir="/pkgbuilddir" -it ppbuilder
