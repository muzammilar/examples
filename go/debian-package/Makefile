# shell variables
date := $(shell date)

# Get the build/release version
ifdef PKG_VERSION
PKG_VERSION := $(PKG_VERSION)
else
PKG_VERSION := 0.0.1-0
endif

# program name
PERIODIC_PRINTER = periodicprinter

# Package Info
PKG_USER = pprintuser
PKG_NAME = periodicprinterpkg

# Install locations
PREF_PKG_BIN = /usr/local
PREF_PKG_LOG = /var/log/periodicprinter

# exports
export PERIODIC_PRINTER
export PKG_NAME
export PREF_PKG_LOG

# Makefile
.PHONY: all clean format lint ${PERIODIC_PRINTER} install uninstall
all: clean ${PERIODIC_PRINTER}

clean:
	-rm -f ${PERIODIC_PRINTER}
	-rm -f debian/${PKG_NAME}

${PERIODIC_PRINTER}:
	go build -ldflags "-X main.date=${date}" -o build/${PERIODIC_PRINTER} ./cmd/pprinter/pprinter.go

lint:
	golint ./...

format:
	goimports -l -w .

# install is called by the package builder
install:
	install -v -d build/${PERIODIC_PRINTER} $(DESTDIR)${PREF_PKG_BIN}/bin/${PERIODIC_PRINTER}
	install -v -d $(DESTDIR)${PREF_PKG_LOG}

uninstall:
	-rm -f $(DESTDIR)$(PREF_PKG_BIN)/bin/${PERIODIC_PRINTER}
	-rm -f $(DESTDIR)$(PREF_PKG_LOG)

# debian building
# note: we create a deb changelog here because it's an example. Ideally, the changelog file should be only incremented with `dch -i`
deb: deb-changelog-create deb-build

deb-changelog-create:
	dch --create --package ${PKG_NAME}
	dch -v ${PKG_VERSION}

deb-build:
	dpkg-buildpackage -us -uc
