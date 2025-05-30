COMMIT := $(shell /usr/bin/git describe --always)
DATE := $(shell /bin/date -u +"%Y-%m-%d-%H:%M")
DISTRO := $(shell lsb_release -cs)

export GOPRIVATE=*.fake.com

# Test Packages
COBERTURAPACKAGE := github.com/boumenot/gocover-cobertura
GO_SQLC_PACKAGE := github.com/sqlc-dev/sqlc/cmd/sqlc
GOIMPORTSPATH := ~/go/bin/goimports
GOIMPORTSPACKAGE := golang.org/x/tools/cmd/goimports@latest

# programs created by this Makefile
MYAPP=build/myapp

# Internal variables
TEST_FLAG :=
BUILD_FLAG :=

# Version check
ifdef CI_COMMIT_TAG
CI_COMMIT_TAG := $(CI_COMMIT_TAG)
else
CI_COMMIT_TAG := 0.0.1rc1
endif


.PHONY: all clean modtidy generate format test-unit test-integration benchmark-unit benchmark-integration
# By default, make starts with the first target (not targets whose names start with ‘.’
# unless they also contain one or more ‘/’). This is called the default goal.
default: clean modtidy generate goimports format test ${MYAPP}

# all target generates the dependencies and then runs the default target
all: generate-deps default

modinit:
	go mod init myapp

clean:
	-rm -f ${MYAPP}

${MYAPP}: generate
	GOPRIVATE=$(GOPRIVATE) go build ${BUILD_FLAG} -ldflags "-X main.commit=${COMMIT} -X main.date=${DATE} -X main.version=${CI_COMMIT_TAG}" -o ${MYAPP} ./cmd/app/app.go

# tidy up go.mod and go.sum files
modtidy:
	GOPRIVATE=$(GOPRIVATE) go mod download
	GOPRIVATE=$(GOPRIVATE) go mod tidy

lint:
	golint ./...

# format code according to go standards
format: formatcheck
	gofmt -l -s -w .

# show gofmt diff instead of writing
formatcheck:
	gofmt -l -d -s .

# validate formatings
formatvalidate: formatcheck format
	echo "Checking for uncommited changes (using git) after running go formatter."
	echo "Uncommited changes (i.e. autogenerated code may be different from the checked in code):"
	git --no-pager diff --color --exit-code

# generate dependencies (separated from generate since install command sometimes take time)
generate-deps:
	go install ${GO_SQLC_PACKAGE}

# auto generate - go
generate:
	go generate ./...

# validate autogenerated go
generate-validate: generate
	echo "Checking for uncommited changes (using git) after running go generate."
	echo "Uncommited changes (i.e. autogenerated code may be different from the checked in code):"
	git --no-pager diff --color --exit-code

goimports:
	go install ${GOIMPORTSPACKAGE}
	${GOIMPORTSPATH} -l -w .

test: test-unit test-integration benchmark-unit benchmark-integration

test-unit: generate
#	go test $(TEST_FLAG) -race ./...
#	mockery
	go test ${TEST_FLAG} --tags=test_unit -cpu 1 -benchmem -benchtime 10000x -bench=. ./...

benchmark-unit: generate
	go test ${TEST_FLAG} --tags=test_unit -cpu 1 -benchmem -benchtime 10000x -bench=. ./...

test-integration: generate
	go test ${TEST_FLAG} --tags=test_integration -cpu 1 -benchmem -benchtime 10000x -bench=. ./...

benchmark-integration: generate
	go test ${TEST_FLAG} --tags=test_integration -cpu 1 -benchmem -benchtime 10000x -bench=. ./...

# test coverage report
# race flag should be used with -covermode atomic
# and is slower than -covermode count
test-coverage:
	go test ./... -coverprofile=coverage.txt -covermode count
	go tool cover -func=coverage.txt
	go get ${COBERTURAPACKAGE}
	go run ${COBERTURAPACKAGE} < coverage.txt > coverage.xml

install: ${MYAPP}
	install -v -d $(DESTDIR)$(PATH_PKG_BIN)
	install -v -d $(DESTDIR)$(PATH_PKG_BIN)/bin
	install -v -m 750 ${MYAPP} $(DESTDIR)$(PATH_PKG_BIN)/bin
# configs directory
	install -v -d $(DESTDIR)$(PATH_PKG_CONF)

# Do not remove configurations and logs and the user
# This is because we might be installing a new version soon
uninstall:
	-rm -f $(DESTDIR)$(prefix)/bin/*
