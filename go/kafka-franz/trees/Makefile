## Variables
PWD := $(shell pwd)
# number of processes for benchmark testing (a list can be provided)
BENCH_CPUS := 1,2,4,8,16,32,64,128,256,512,1024
# number of iterations for benchmark testing - no whitespace after variable
# If comment is added after number below, that would add a space
#BENCH_ITERATIONS :=1000 # extra space added
#BENCH_ITERATIONS :=1000# no extra space added
BENCH_ITERATIONS :=50000
# Parent Directory
PARENT_PATH:=$(abspath ..)

GO:= go

## Builds
.PHONY: all clean test lint goget

all: clean test

clean:
	echo "Nothing to clean in ${PWD}"

test:
# check for raceconditions
	${GO} test -race ./...
# run the benchmark tests
	${GO} test -cpu ${BENCH_CPUS}  -benchmem -benchtime ${BENCH_ITERATIONS}x -bench=. ./...
# run the benchmark tests without optimzations (-gcflags '-N' should disable compiler optimzations)
	${GO} test -gcflags '-N' -cpu ${BENCH_CPUS} -benchmem -benchtime ${BENCH_ITERATIONS}x -bench=. ./...

lint:
	golint ./...

# add module requirements
goget:
	${GO} get -v ./...

modtidy:
	${GO} mod download
	${GO} mod tidy
