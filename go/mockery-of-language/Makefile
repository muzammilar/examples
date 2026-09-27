## Variables
PWD := $(shell pwd)

GOPRIVATE:= # no go private for now

# Build Variables
DATE := $(shell /bin/date -u +"%Y-%m-%d-%H:%M")

# programs created by this Makefile
MYAPP=build/myapp

# golang's formatting package
IMPORTS_PATH := ~/go/bin/goimports
IMPORTS_PACKAGE := golang.org/x/tools/cmd/goimports@latest

# golang's generate package
GO_MOCKERY_PACKAGE := github.com/vektra/mockery/v2
GO_MOCKERY_PACKAGE_VERSION := v2.45.0

# Parent Directory
PARENT_PATH:=$(abspath ..)

## Builds
.PHONY: all clean generate test lint goget goimports modtidy

## First non-phony target is the default
default: all

all: clean modtidy generate mockery lsdir test ${MYAPP}

${MYAPP}: generate
	GOPRIVATE=${GOPRIVATE} go build ${BUILD_FLAG} -ldflags "-X main.date=${DATE}" -o ${MYAPP} ./cmd/main.go

clean:
	echo "Nothing to clean in ${PWD}"

generate-deps:
	go install ${GO_MOCKERY_PACKAGE}@${GO_MOCKERY_PACKAGE_VERSION}

generate: generate-deps
	go generate ./...

lsdir:
	ls -alR

mockery:
	mockery

test:
# check for raceconditions
	go test -verbose -race ./...

lint:
	golint ./...

modtidy:
	GOPRIVATE=${GOPRIVATE} go mod tidy

# add module requirements
goget:
	GOPRIVATE=${GOPRIVATE} go get -v ./...

goimports:
	go install ${IMPORTS_PACKAGE}
	${IMPORTS_PATH} -l -w .
